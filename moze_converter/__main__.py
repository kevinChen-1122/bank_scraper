"""
bank_scraper/moze_converter/__main__.py
CLI 手動補轉換（不重新爬取，只用 downloads/ 現有 CSV）

用法：
  python -m moze_converter --date 2026-06-11
  python -m moze_converter --from 2026-05-01 --to 2026-05-31
  python -m moze_converter               # 不篩選，輸出全部
"""

import argparse
from datetime import date as _date, datetime as _dt

from .converter import convert_to_moze, select_relevant_files


def _parse_date(s: str) -> _date:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return _dt.strptime(s, fmt).date()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(f"日期格式錯誤（應為 YYYY-MM-DD 或 YYYY/MM/DD）：{s}")


def main():
    parser = argparse.ArgumentParser(
        prog="python -m moze_converter",
        description="手動補轉換銀行 CSV → MOZE 格式",
        epilog=(
            "範例：\n"
            "  python -m moze_converter --date 2026-06-11\n"
            "  python -m moze_converter --from 2026-05-01 --to 2026-05-31\n"
            "  python -m moze_converter               # 不篩選，輸出全部"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--date", type=_parse_date, metavar="YYYY-MM-DD",
                        help="只輸出單一日期")
    parser.add_argument("--from", dest="date_from", type=_parse_date, metavar="YYYY-MM-DD",
                        help="起始日期（含）")
    parser.add_argument("--to",   dest="date_to",   type=_parse_date, metavar="YYYY-MM-DD",
                        help="結束日期（含）")
    args = parser.parse_args()

    if args.date and (args.date_from or args.date_to):
        parser.error("--date 不能與 --from / --to 同時使用")

    if args.date:
        date_from = date_to = args.date
    else:
        date_from = args.date_from
        date_to   = args.date_to

    from config import OUTPUT_DIR, MOZE_DIR
    bank_files = select_relevant_files(OUTPUT_DIR, date_from, date_to)
    if not bank_files:
        print(f"找不到銀行 CSV（{OUTPUT_DIR}）")
        raise SystemExit(1)
    total_files = len(list(OUTPUT_DIR.rglob("*.csv")))
    if len(bank_files) < total_files:
        print(f"[載入] 略過 {total_files - len(bank_files)} 個日期不相關的舊 CSV")

    if date_from and date_from == date_to:
        # 單天 → moze/YYYY/MM/MOZE_YYYYMMDD.csv
        out = MOZE_DIR / date_from.strftime("%Y") / date_from.strftime("%m") / f"MOZE_{date_from.strftime('%Y%m%d')}.csv"
    elif date_from or date_to:
        # 跨日區間 → moze/MOZE_YYYYMMDD_YYYYMMDD.csv
        tag = f"{(date_from or _date.min).strftime('%Y%m%d')}_{(date_to or _date.max).strftime('%Y%m%d')}"
        out = MOZE_DIR / f"MOZE_{tag}.csv"
    else:
        out = MOZE_DIR / "MOZE_all.csv"

    convert_to_moze(bank_files, out, date_from=date_from, date_to=date_to)


if __name__ == "__main__":
    main()
