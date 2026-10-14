"""
bank_scraper/main.py
每日銀行明細自動下載 & 轉換 & 傳送

使用方式:
  python main.py                                   # 抓昨天（正常排程）
  python main.py --date 2026-06-10                 # 補單天（用現有 CSV）
  python main.py --from 2026-06-01 --to 2026-06-10 # 補區間（用現有 CSV）
"""

import argparse
import asyncio
import io
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from config import OUTPUT_DIR, MOZE_DIR, LOGS_DIR, CHROME_DEBUG_PORT, FILES_KEEP_YEARS, CATHAY_ACCOUNTS
from browser import start_debug_chrome, run_with_profile
from scraper_ctbc import scrape_ctbc
from scraper_cathay import scrape_cathay
from moze_converter import convert_to_moze, cleanup_old_files, select_relevant_files
from discord_sender import send_files, send_error_report

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class _TeeStream:
    """同時寫入兩個 stream（用於捕捉 stderr 供 Discord 回報）"""
    def __init__(self, primary, secondary):
        self.primary   = primary
        self.secondary = secondary

    def write(self, data):
        self.primary.write(data)
        self.secondary.write(data)

    def flush(self):
        self.primary.flush()
        self.secondary.flush()


LOG_MAX_BYTES  = 5  * 1024 * 1024   # 超過此大小才觸發輪替
LOG_KEEP_BYTES = 1  * 1024 * 1024   # 輪替後保留最近這麼多 bytes


def _rotate_log(path: Path, max_bytes: int = LOG_MAX_BYTES, keep_bytes: int = LOG_KEEP_BYTES):
    """
    launchd 用 append 模式把 stdout/stderr 導到 logs/*.log，且從不輪替，
    會無限成長。這裡在原地截斷同一個 inode（不是刪除重建），append 模式
    的檔案描述符之後寫入時一律接在目前的 EOF，所以截斷後續寫入仍然安全。
    """
    if not path.exists() or path.stat().st_size <= max_bytes:
        return
    tail = path.read_bytes()[-keep_bytes:]
    newline_idx = tail.find(b"\n")
    if newline_idx != -1:
        tail = tail[newline_idx + 1:]
    path.write_bytes("[... 較舊的紀錄已自動清除 ...]\n".encode("utf-8") + tail)
    print(f"[清理] {path.name} 超過 {max_bytes // 1024 // 1024}MB，已截斷保留最近內容")


def _moze_output_path(date_from: date, date_to: date) -> Path:
    if date_from == date_to:
        return MOZE_DIR / date_from.strftime("%Y") / date_from.strftime("%m") / f"MOZE_{date_from.strftime('%Y%m%d')}.csv"
    tag = f"{date_from.strftime('%Y%m%d')}_{date_to.strftime('%Y%m%d')}"
    return MOZE_DIR / f"MOZE_{tag}.csv"


async def _scrape(target: datetime) -> tuple[list[Path], bool]:
    all_bank_files: list[Path] = []
    ctbc_files: list[Path] = []
    ctbc_failed = False
    stealth = Stealth(
        navigator_platform_override="MacIntel",
        navigator_languages_override=("zh-TW", "zh"),
        chrome_runtime=True,
    )

    async with stealth.use_async(async_playwright()) as p:
        chrome_proc = None
        try:
            chrome_proc = start_debug_chrome()
            browser = await p.chromium.connect_over_cdp(f"http://localhost:{CHROME_DEBUG_PORT}")
            context  = browser.contexts[0] if browser.contexts else await browser.new_context(accept_downloads=True)
            page     = await context.new_page()
            ctbc_files = await scrape_ctbc(page, target)
            all_bank_files.extend(ctbc_files)
            await page.close()
            await browser.close()
        except Exception as e:
            print(f"[中信] ❌ 錯誤：{e}", file=sys.stderr)
            ctbc_failed = True
        finally:
            if chrome_proc:
                chrome_proc.terminate()
                chrome_proc.wait()
                print("[中信] Chrome 已關閉")

        # 國泰爬蟲偶爾會在登入或下載階段失敗（網路波動、銀行網站當下較慢
        # 等一次性因素），失敗後用全新瀏覽器重試即可恢復，這裡最多嘗試 3 次。
        cathay_files: list[Path] = []
        for attempt in range(3):
            cathay_files = await run_with_profile(
                p, lambda page: scrape_cathay(page, target), "國泰"
            )
            if len(cathay_files) >= len(CATHAY_ACCOUNTS):
                break
            if attempt < 2:
                print(f"[國泰] ⚠️ 未完整下載（第 {attempt + 1} 次），等待後以全新瀏覽器重試...", file=sys.stderr)
                await asyncio.sleep(5)
        all_bank_files.extend(cathay_files)

    all_ok = not ctbc_failed and len(cathay_files) == len(CATHAY_ACCOUNTS)
    return all_bank_files, all_ok


async def main(date_from: date, date_to: date, backfill: bool):
    if backfill:
        label = str(date_from) if date_from == date_to else f"{date_from} ~ {date_to}"
        print(f"[補檔] 使用現有 CSV 補轉換：{label}")
        all_bank_files = select_relevant_files(OUTPUT_DIR, date_from, date_to)
        if not all_bank_files:
            print("⚠️ downloads/ 找不到對應的銀行 CSV，請先確認檔案存在", file=sys.stderr)
            return
        print(f"[補檔] 找到 {len(all_bank_files)} 個銀行 CSV：")
        for f in all_bank_files:
            print(f"   {f}")
    else:
        target = datetime(date_to.year, date_to.month, date_to.day)
        all_bank_files, scrape_ok = await _scrape(target)
        if not all_bank_files:
            print("⚠️ 沒有下載到任何檔案，略過後續步驟", file=sys.stderr)
            return
        print(f"\n✅ 爬蟲完成，共下載 {len(all_bank_files)} 個銀行 CSV：")
        for f in all_bank_files:
            print(f"   {f}")

    # ── 轉換成 MOZE 格式 ─────────────────────────────────────
    moze_file, row_count = convert_to_moze(
        all_bank_files,
        _moze_output_path(date_from, date_to),
        date_from=date_from,
        date_to=date_to,
    )

    # ── 傳送到 Discord（僅 MOZE CSV）────────────────────────
    if date_from == date_to:
        label = date_from.strftime("%Y/%m/%d")
    else:
        label = f"{date_from.strftime('%Y/%m/%d')} ~ {date_to.strftime('%Y/%m/%d')}"
    if not backfill and not scrape_ok:
        print("[傳送] ⚠️ 有銀行帳戶下載失敗，略過 MOZE 傳送", file=sys.stderr)
    elif row_count == 0:
        print("[傳送] 當日無交易記錄，傳送通知（不附檔案）")
        send_files([], label=label, backfill=backfill, count=0)
    else:
        send_files([moze_file], label=label, backfill=backfill, count=row_count)

    # ── 清理過期檔案（僅正常排程模式）───────────────────────
    if not backfill:
        for directory, label in [(OUTPUT_DIR, "銀行原始 CSV"), (MOZE_DIR, "MOZE CSV")]:
            deleted = cleanup_old_files(directory, keep_years=FILES_KEEP_YEARS)
            if deleted:
                print(f"[清理] 刪除 {len(deleted)} 個超過 {FILES_KEEP_YEARS} 年的{label}：")
                for f in deleted:
                    print(f"   {f.name}")


def _parse_date(s: str) -> date:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"日期格式錯誤（應為 YYYY-MM-DD）：{s}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="銀行明細自動下載 & 轉換 & 傳送",
        epilog=(
            "範例：\n"
            "  python main.py                                   # 抓昨天\n"
            "  python main.py --date 2026-06-10                 # 補單天\n"
            "  python main.py --from 2026-06-01 --to 2026-06-10 # 補區間\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--date", type=_parse_date, metavar="YYYY-MM-DD",
                        help="補單天（用現有 CSV，不重新抓取）")
    parser.add_argument("--from", dest="date_from", type=_parse_date, metavar="YYYY-MM-DD",
                        help="補區間起始日（含）")
    parser.add_argument("--to",   dest="date_to",   type=_parse_date, metavar="YYYY-MM-DD",
                        help="補區間結束日（含）")
    args = parser.parse_args()

    if args.date and (args.date_from or args.date_to):
        parser.error("--date 不能與 --from / --to 同時使用")

    if args.date:
        df = dt = args.date
        backfill = True
    elif args.date_from or args.date_to:
        df = args.date_from or args.date_to
        dt = args.date_to   or args.date_from
        backfill = True
    else:
        yesterday = (datetime.now() - timedelta(days=1)).date()
        df = dt = yesterday
        backfill = False

    _stdout_buf = io.StringIO()
    _stderr_buf = io.StringIO()
    sys.stdout  = _TeeStream(sys.__stdout__, _stdout_buf)
    sys.stderr  = _TeeStream(sys.__stderr__, _stderr_buf)

    try:
        asyncio.run(main(df, dt, backfill))
    finally:
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        errors = _stderr_buf.getvalue().strip()
        if errors:
            send_error_report(errors, full_log=_stdout_buf.getvalue().strip())

        _rotate_log(LOGS_DIR / "stdout.log")
        _rotate_log(LOGS_DIR / "stderr.log")
