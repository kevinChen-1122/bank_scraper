"""
bank_scraper/browser.py
Playwright / Chrome 瀏覽器工作階段管理
"""

import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeout

from config import BASE_DIR, CHROME_BIN, CHROME_PROFILE, CHROME_DEBUG_PORT, CATHAY_PROFILE

# 失敗時的截圖/HTML 快照，方便事後回頭確認當下頁面實際長相
# 注意：登入後頁面可能含真實帳戶餘額等資訊，跟 downloads/ 的銀行 CSV 一樣只受本機安全性保護
DIAGNOSTICS_DIR = BASE_DIR / "diagnostics"

# run_with_profile() 單次嘗試的最長容許時間。曾發生 Chrome 渲染程序卡死
# （CPU 100% 但完全無回應、連 SIGTERM 都殺不掉，只有 SIGKILL 有效）超過
# 一小時，main.py 因為 await 永遠不返回而整個卡住，導致當天排程完全沒有
# 執行紀錄，需要人工發現並手動終止才能恢復。這個逾時確保就算瀏覽器真的
# 卡死，也會在有限時間內放棄並強制清理，讓外層的重試機制能接手。
SCRAPE_HANG_TIMEOUT = 90


async def capture_diagnostics(page, bank_name: str, label: str):
    """發生錯誤時保存截圖與頁面 HTML，供事後排查（兩家銀行共用）"""
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        await page.screenshot(path=DIAGNOSTICS_DIR / f"{ts}_{label}.png", full_page=True)
        html = await page.content()
        (DIAGNOSTICS_DIR / f"{ts}_{label}.html").write_text(html, encoding="utf-8")
        print(f"[{bank_name}] 🩺 已保存診斷截圖/HTML：{ts}_{label}（當下網址：{page.url}）", file=sys.stderr)
    except Exception as e:
        print(f"[{bank_name}] ⚠️ 保存診斷資料失敗：{e}", file=sys.stderr)


def install_dialog_logger(page, bank_name: str):
    """記錄並自動關閉頁面跳出的 alert/confirm 等原生對話框，同時把訊息寫進 stderr 供事後查看"""
    async def _on_dialog(dialog):
        print(f"[{bank_name}] ⚠️ 頁面跳出對話框（{dialog.type}）：{dialog.message}", file=sys.stderr)
        await dialog.dismiss()

    page.on("dialog", lambda dialog: asyncio.ensure_future(_on_dialog(dialog)))


def _clear_crash_flag(profile_dir: Path):
    """
    若上次執行意外中斷（segfault／被強制關閉），Chrome 會把 profile 的
    exit_type 標記為 "Crashed"，下次啟動時觸發的復原流程本身若剛好也
    有問題，會在同一個位置再次當機，形成每天重播同一個崩潰的循環
    （曾連續 3 天在完全相同的程式碼位置 segfault）。啟動前主動把這個
    標記重設回正常，讓 Chrome 不會嘗試做任何 session 復原。
    """
    prefs_path = profile_dir / "Default" / "Preferences"
    if not prefs_path.exists():
        return
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        profile_state = prefs.get("profile", {})
        if profile_state.get("exit_type") == "Crashed":
            profile_state["exit_type"] = "Normal"
            profile_state["exited_cleanly"] = True
            prefs["profile"] = profile_state
            prefs_path.write_text(json.dumps(prefs), encoding="utf-8")
            print("[Browser] 偵測到 Chrome 上次異常結束，已重設狀態避免復原流程再次觸發當機")
    except Exception as e:
        print(f"[Browser] ⚠️ 重設 Chrome 崩潰標記失敗：{e}", file=sys.stderr)


def start_debug_chrome() -> subprocess.Popen:
    """啟動帶 remote debug port 的獨立 Chrome，回傳 process"""
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    _clear_crash_flag(CHROME_PROFILE)
    proc = subprocess.Popen(
        [
            CHROME_BIN,
            f"--remote-debugging-port={CHROME_DEBUG_PORT}",
            f"--user-data-dir={CHROME_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        try:
            urllib.request.urlopen(
                f"http://localhost:{CHROME_DEBUG_PORT}/json/version", timeout=1
            )
            print(f"[Browser] Chrome debug port {CHROME_DEBUG_PORT} 就緒")
            return proc
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"Chrome 啟動逾時，無法連接 port {CHROME_DEBUG_PORT}")


async def run_with_profile(p, scrape_fn, bank_name: str) -> list[Path]:
    """
    用 persistent context 執行爬蟲。

    刻意不指定 channel，改用 Playwright 內建的 Chromium 而非系統安裝的真實
    Google Chrome。系統 Chrome 在這台機器的 macOS 版本上會不定期 SIGSEGV
    （崩潰位址呈現典型的「已釋放記憶體」中毒樣式 0xcdcdcdcdcdcdcdcd，是
    Chrome 本身的 use-after-free bug；同類問題可參考
    github.com/microsoft/playwright issue #6024、#31064），調整啟動 flag、
    重建 profile、改用 headless 都無法避開，只有換掉那個有 bug 的二進位檔
    才穩定下來。代價是指紋跟系統 Chrome 略有差異，需留意是否觸發銀行的
    反爬蟲偵測。
    """
    CATHAY_PROFILE.mkdir(parents=True, exist_ok=True)
    _clear_crash_flag(CATHAY_PROFILE)
    context = await p.chromium.launch_persistent_context(
        str(CATHAY_PROFILE),
        headless=False,
        accept_downloads=True,
        slow_mo=50,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-extensions",
            "--disable-client-side-phishing-detection",
        ],
        ignore_default_args=["--enable-automation"],
        viewport={"width": 1280, "height": 800},
        locale="zh-TW",
        timezone_id="Asia/Taipei",
    )
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['zh-TW', 'zh', 'en']});
        window.chrome = { runtime: {} };
    """)
    def _page_alive() -> bool:
        # Chrome 若已當機（segfault），page/context 早已失聯，
        # 連 page.is_closed() 本身都可能拋例外，這裡一併吞掉。
        try:
            return page is not None and not page.is_closed()
        except Exception:
            return False

    page = None
    try:
        page = await context.new_page()
        install_dialog_logger(page, bank_name)
        return await asyncio.wait_for(scrape_fn(page), timeout=SCRAPE_HANG_TIMEOUT)
    except asyncio.TimeoutError:
        # 瀏覽器整個卡死（渲染程序無回應）時會走到這裡；此時任何後續操作
        # （截圖、context.close()）大概率也會卡住，所以不嘗試留存診斷資料，
        # 直接放棄並在 finally 強制清理，避免拖長卡住的時間。
        print(f"[{bank_name}] ❌ 逾時 {SCRAPE_HANG_TIMEOUT} 秒仍未完成，瀏覽器可能已卡死，強制終止", file=sys.stderr)
        return []
    except PlaywrightTimeout as e:
        print(f"[{bank_name}] ❌ Timeout：{e}", file=sys.stderr)
        if _page_alive():
            await capture_diagnostics(page, bank_name, "timeout")
        return []
    except Exception as e:
        print(f"[{bank_name}] ❌ 錯誤：{e}", file=sys.stderr)
        if _page_alive():
            await capture_diagnostics(page, bank_name, "error")
        return []
    finally:
        try:
            await asyncio.wait_for(context.close(), timeout=10)
        except Exception:
            # context.close() 本身也可能因為瀏覽器無回應而卡住，
            # 直接在系統層級強制殺掉這個 profile 底下所有 Chrome 程序，
            # 確保下一次重試時 profile 的 SingletonLock 不會卡住新的啟動。
            print(f"[{bank_name}] ⚠️ 正常關閉逾時，強制終止此 profile 的所有 Chrome 程序", file=sys.stderr)
            subprocess.run(["pkill", "-KILL", "-f", str(CATHAY_PROFILE)], capture_output=True)
