"""
bank_scraper/scraper_ctbc.py
中國信託網銀爬蟲：下載活存帳戶昨日明細
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeout

from config import OUTPUT_DIR, CHROME_DEBUG_PORT
from keychain import get_credentials
from browser import capture_diagnostics, install_dialog_logger

LOGIN_URL   = "https://www.ctbcbank.com/twrbc/twrbc-general/ot001/010"
DEPOSIT_URL = "https://www.ctbcbank.com/twrbc/twrbc-deposit/qu002/010"


async def _ctbc_confirm_login_if_needed(page) -> bool:
    """
    登入送出後，若出現「確認登入」彈窗就點擊。
    回傳 True 表示有點擊，False 表示彈窗未出現。
    """
    await page.wait_for_timeout(2000)  # 給彈窗時間渲染
    clicked = await page.evaluate("""
        (() => {
            const btn = [...document.querySelectorAll('a, button')]
                .filter(el => el.offsetParent !== null)
                .find(el => el.innerText.trim() === '確認登入');
            if (btn) { btn.click(); return true; }
            return false;
        })()
    """)
    return bool(clicked)


async def _ctbc_ensure_logged_in(page, creds: dict) -> bool:
    """
    前往登入頁，若尚未登入則完成登入流程；若 debug profile 仍帶有前次有效
    session，登入頁會直接被導回帳戶總覽頁，此時偵測到已登入就略過登入步驟。

    回傳 True 表示這次是全新走完登入流程，False 表示沿用既有 session。
    scrape_ctbc()（正式排程）與 --login-only 診斷模式共用同一份邏輯，
    避免兩處各自維護一份登入程式碼、修 bug 時忘記同步更新。
    """
    install_dialog_logger(page, "中信")

    print("[中信] 前往登入頁...")
    await page.goto(LOGIN_URL, wait_until="networkidle")

    already_logged_in = await page.locator('[class*="asset"]').count() > 0
    if not already_logged_in:
        for attempt in range(2):
            try:
                await page.wait_for_selector('input[formcontrolname="custIxd"]', timeout=20000)
                break
            except PlaywrightTimeout:
                already_logged_in = await page.locator('[class*="asset"]').count() > 0
                if already_logged_in:
                    break
                if attempt == 0:
                    # 逾時原因可能是登入頁載入較慢，也可能是被導向到別的頁面
                    # （例如中信官網首頁）；重新 goto 登入網址，而不是單純 reload
                    # 目前頁面，否則如果已經被導到別的頁面，reload 只會一直停在
                    # 錯誤頁面，永遠不會真的回去嘗試登入頁。
                    print(
                        f"[中信] 登入頁載入逾時（目前網址：{page.url}），重新導向登入頁後重試...",
                        file=sys.stderr,
                    )
                    await page.goto(LOGIN_URL, wait_until="networkidle")
                else:
                    await capture_diagnostics(page, "中信", "login_page_timeout")
                    raise

    if already_logged_in:
        print("[中信] 偵測到既有登入 session，略過登入步驟")
        return False

    await page.locator('input[formcontrolname="custIxd"]').click()
    await page.keyboard.type(creds["id_number"], delay=80)
    await page.locator('input[formcontrolname="userIxd"]').click()
    await page.keyboard.type(creds["username"], delay=80)
    await page.locator('input[formcontrolname="pxd"]').click()
    await page.keyboard.type(creds["password"], delay=80)

    await page.wait_for_timeout(500)
    await page.click('a.btn_submit')

    if await _ctbc_confirm_login_if_needed(page):
        print("[中信] 點擊確認登入")

    try:
        await page.wait_for_selector('[class*="asset"]', timeout=30000)
    except PlaywrightTimeout:
        # 帳密送出後頁面沒有正常跳轉到總覽頁，把當下畫面存下來方便回頭確認
        await capture_diagnostics(page, "中信", "post_login_redirect_timeout")
        raise
    print("[中信] 登入完成")
    return True


async def scrape_ctbc(page, yesterday: datetime) -> list[Path]:
    """登入中信網銀，下載活存帳戶昨日明細，回傳下載檔案路徑清單"""
    creds    = get_credentials("ctbc")
    date_str = yesterday.strftime("%Y%m%d")
    downloaded: list[Path] = []

    await _ctbc_ensure_logged_in(page, creds)

    await page.goto(DEPOSIT_URL, wait_until="networkidle")
    await page.wait_for_selector('li.nav-item', timeout=15000)
    await page.wait_for_timeout(1000)

    yesterday_ym = yesterday.strftime("%Y/%m")
    await page.locator(f'li.nav-item:has-text("{yesterday_ym}")').first.click()
    output_path = OUTPUT_DIR / yesterday.strftime("%Y") / yesterday.strftime("%m") / f"ctbc_saving_{date_str}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await page.wait_for_selector('csv-download', timeout=10000)
        async with page.expect_download(timeout=15000) as dl_info:
            await page.click('csv-download')
        download = await dl_info.value
        await download.save_as(output_path)
        downloaded.append(output_path)
        print(f"[中信] 已下載：{output_path}")
    except Exception:
        if await page.locator('text=您查詢的日期區間無明細資料').count() > 0:
            print(f"[中信] 查無明細資料（{yesterday_ym} 無交易）")
        else:
            await capture_diagnostics(page, "中信", "csv_export_failed")
            raise

    try:
        await page.evaluate("document.querySelector('#btnHeaderLogout').click()")
        await page.wait_for_timeout(1500)
        confirmed = await page.evaluate("""
            (() => {
                const btn = [...document.querySelectorAll('a.btn_submit')]
                    .find(el => el.offsetParent !== null && el.innerText.trim() === '確認');
                if (btn) { btn.click(); return true; }
                return false;
            })()
        """)
        await page.wait_for_timeout(1000)
        print(f"[中信] 已登出（確認彈窗：{confirmed}）")
    except Exception as e:
        print(f"[中信] ⚠️ 登出失敗：{e}", file=sys.stderr)

    return downloaded


# ── 單獨執行入口 ─────────────────────────────────────────────
async def _run_standalone(login_only: bool):
    from browser import start_debug_chrome
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    yesterday = datetime.now() - timedelta(days=1)
    stealth = Stealth(
        navigator_platform_override="MacIntel",
        navigator_languages_override=("zh-TW", "zh"),
        chrome_runtime=True,
    )

    chrome_proc = None
    try:
        async with stealth.use_async(async_playwright()) as p:
            chrome_proc = start_debug_chrome()
            browser = await p.chromium.connect_over_cdp(f"http://localhost:{CHROME_DEBUG_PORT}")
            context  = browser.contexts[0] if browser.contexts else await browser.new_context(accept_downloads=True)
            page     = await context.new_page()

            if login_only:
                creds = get_credentials("ctbc")
                try:
                    fresh_login = await _ctbc_ensure_logged_in(page, creds)
                    print(f"[中信] ✅ {'登入成功' if fresh_login else '已經是登入狀態'}")
                except Exception:
                    print("[中信] ❌ 登入失敗或超時，請看瀏覽器畫面")

                print("[中信] 瀏覽器保持開啟，按 Ctrl+C 關閉")
                await asyncio.sleep(3600)
            else:
                files = await scrape_ctbc(page, yesterday)
                print(f"\n完成，下載 {len(files)} 個檔案：")
                for f in files:
                    print(f"  {f}")

            await page.close()
            await browser.close()

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n[中信] 收到中斷訊號")
    except Exception as e:
        print(f"❌ 錯誤：{e}", file=sys.stderr)
    finally:
        if chrome_proc:
            chrome_proc.terminate()
            chrome_proc.wait()
            print("[中信] Chrome 已關閉")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="中信爬蟲單獨測試")
    parser.add_argument("--login-only", action="store_true",
                        help="只執行登入（完成後暫停，方便觀察是否成功）")
    args = parser.parse_args()

    asyncio.run(_run_standalone(login_only=args.login_only))
