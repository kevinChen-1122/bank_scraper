"""
bank_scraper/moze_converter/converter.py
主流程：挑選相關檔案 → 解析 → 去重 → 篩選日期 → 轉帳配對 → 寫出 MOZE CSV
以及下載/輸出目錄的過期檔案清理
"""

import csv
import re
import shutil
from datetime import date as _date, datetime as _dt, timedelta
from pathlib import Path

from .parsers import detect_parser
from .transform import deduplicate_by_latest_file, pair_transfers

# ── MOZE 欄位順序（官方規範）────────────────────────────
MOZE_COLS = [
    "帳戶", "幣種", "記錄類型", "主類別", "子類別",
    "金額", "手續費", "折扣", "名稱", "商家",
    "日期", "時間", "專案", "描述", "標籤", "對象",
]


def _extract_file_date(path: Path) -> "_date | None":
    """從檔名的 YYYYMMDD 部分解析日期，解析失敗回傳 None"""
    m = re.search(r"(\d{8})", path.stem)
    if m:
        try:
            return _dt.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            pass
    return None


def select_relevant_files(
    directory: Path,
    date_from: "_date | None",
    date_to:   "_date | None",
) -> list[Path]:
    """
    只回傳可能包含 [date_from, date_to] 記錄的 CSV 檔案。
    銀行 CSV 最多涵蓋下載日往前 30 天，所以：
      file_date >= date_from - 30 天  →  可能有 date_from 的資料
      file_date <= date_to + 1 天     →  不可能全部早於 date_to
    無法解析日期的檔案一律保留。同時支援舊的平坦結構與新的 YYYY/MM 結構。
    """
    all_files = sorted(directory.rglob("*.csv"))
    if date_from is None and date_to is None:
        return all_files

    df = date_from or _date.min
    dt = date_to   or _date.max
    result = []
    for f in all_files:
        fd = _extract_file_date(f)
        if fd is None or (fd >= df - timedelta(days=30) and fd <= dt + timedelta(days=1)):
            result.append(f)
    return result


def cleanup_old_files(directory: Path, keep_years: int = 1) -> list[Path]:
    """
    清理過期檔案，保留最近 keep_years 年（滾動計算，非日曆年）。
    - YYYY/ 子目錄：比較目錄最後月份的最後一天是否超過保留期
    - 根目錄下的舊式 CSV：比較檔案修改時間
    回傳已刪除的路徑清單。
    """
    now      = _dt.now()
    cutoff   = now - timedelta(days=keep_years * 365)
    cutoff_ts = cutoff.timestamp()
    deleted: list[Path] = []

    for item in sorted(directory.iterdir()):
        if item.is_dir() and re.fullmatch(r"\d{4}", item.name):
            year = int(item.name)
            # 以該年最後一天（12/31）判斷整個年份資料夾是否過期
            year_end = _dt(year, 12, 31)
            if year_end < cutoff:
                shutil.rmtree(item)
                deleted.append(item)
        elif item.is_file() and item.suffix == ".csv":
            if item.stat().st_mtime < cutoff_ts:
                item.unlink()
                deleted.append(item)

    return deleted


def _record_date(r: dict) -> _date:
    """將記錄的日期字串 'YYYY/M/D' 轉成 date 物件"""
    y, m, d = r["日期"].split("/")
    return _date(int(y), int(m), int(d))


def convert_to_moze(
    bank_csv_paths: list[Path],
    output_path: Path,
    date_from: "_date | None" = None,
    date_to:   "_date | None" = None,
) -> tuple[Path, int]:
    """
    將一或多個銀行 CSV 轉成單一 MOZE 格式 CSV。

    Args:
        bank_csv_paths: 銀行匯出的 CSV 路徑清單
        output_path:    輸出的 MOZE.csv 路徑
        date_from:      篩選起始日（含，None 表示不限）
        date_to:        篩選結束日（含，None 表示不限）

    Returns:
        (output_path, 筆數)
    """
    tagged: list[tuple[dict, "_date"]] = []

    for path in bank_csv_paths:
        parser_fn, account_name = detect_parser(path)
        if parser_fn is None:
            print(f"[轉換] ⚠️ 無法識別檔案類型，略過：{path.name}")
            continue
        file_date = _extract_file_date(path) or _date.min
        records = parser_fn(path, account_name)
        print(f"[轉換] {path.name} → {len(records)} 筆（{account_name}）")
        tagged.extend((r, file_date) for r in records)

    all_records = deduplicate_by_latest_file(tagged)

    if date_from is not None or date_to is not None:
        df = date_from or _date.min
        dt = date_to   or _date.max
        before = len(all_records)
        all_records = [r for r in all_records if df <= _record_date(r) <= dt]
        label = (
            str(date_from) if date_from == date_to
            else f"{date_from} ~ {date_to}"
        )
        print(f"[轉換] 篩選 {label}：{before} → {len(all_records)} 筆")

    all_records = pair_transfers(all_records)

    paired = sum(
        1 for r in all_records if r["記錄類型"] in ("轉出", "轉入")
    ) // 2
    if paired:
        print(f"[轉換] 🔗 偵測到 {paired} 組內部轉帳配對")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MOZE_COLS)
        writer.writeheader()
        writer.writerows(all_records)

    print(f"[轉換] ✅ 共 {len(all_records)} 筆 → {output_path}")
    return output_path, len(all_records)
