"""
bank_scraper/moze_converter/parsers.py
銀行匯出 CSV 的編碼修復與逐行解析

支援：
  - 中國信託  ctbc_saving_YYYYMMDD.csv       (Big5, CRLF)
  - 國泰世華  cathay_活期儲蓄存款_YYYYMMDD.csv  (UTF-8 BOM, 多行欄位)
  - 國泰世華  cathay_數位存款_原KOKO_YYYYMMDD.csv
"""

import csv
import re
from pathlib import Path

from .categories import determine_type, get_category

_KNOWN_ENCODINGS = ("utf-8-sig", "utf-8", "big5", "cp950")


def ensure_utf8(path: Path) -> str:
    """
    偵測 CSV 檔案編碼，若非 UTF-8 則原地轉換為 UTF-8 BOM。
    回傳偵測到的原始編碼名稱。
    """
    raw = path.read_bytes()
    detected = "utf-8"
    for enc in _KNOWN_ENCODINGS:
        try:
            raw.decode(enc)
            detected = enc
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if detected not in ("utf-8-sig", "utf-8"):
        path.write_text(raw.decode(detected), encoding="utf-8-sig")
        print(f"[編碼] {path.name}：{detected} → UTF-8 BOM")

    return detected


def _amount_int(s: str) -> int | None:
    """'15,000' → 15000，'−' / '' → None"""
    s = s.strip().replace(",", "").replace("，", "")
    if s in ("−", "-", "", "－"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _fmt_date(d: str) -> str:
    """'2026/06/12' → '2026/6/12'（MOZE 格式）"""
    parts = d.strip().split("/")
    if len(parts) == 3:
        return f"{parts[0]}/{int(parts[1])}/{int(parts[2])}"
    return d


def parse_cathay(path: Path, account_name: str) -> list[dict]:
    """解析國泰 CSV（多行欄位用 csv.reader 處理）"""
    ensure_utf8(path)
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)

    # 找表頭行
    header_idx = next(
        (i for i, r in enumerate(rows) if r and r[0] == "交易日期"), None
    )
    if header_idx is None:
        return []

    records = []
    for row in rows[header_idx + 1 :]:
        if len(row) < 6:
            continue
        # 交易日期欄可能是 "2026/06/12\n10:49"
        dt_raw = row[0].replace("\n", " ").strip()
        m = re.match(r"(\d{4}/\d{2}/\d{2})\s+(\d{2}:\d{2})", dt_raw)
        if not m:
            continue

        date_str = _fmt_date(m.group(1))
        time_str = m.group(2)
        desc     = row[2].strip() if len(row) > 2 else ""
        出_v     = _amount_int(row[3]) if len(row) > 3 else None
        入_v     = _amount_int(row[4]) if len(row) > 4 else None
        memo     = row[7].strip() if len(row) > 7 else ""

        # 費用沖正、錯誤更正等銀行更正項目的提出欄為負數，實際意義是存入
        if 出_v is not None and 出_v < 0:
            入_v = -出_v
            出_v = None

        rtype             = determine_type(desc, 出_v is not None, 入_v is not None)
        main_cat, sub_cat = get_category(desc)
        amount            = -(出_v or 0) if rtype in ("支出", "轉出") else (入_v or 0)
        name              = memo[:30] if memo and memo != "−" else ""

        records.append({
            "帳戶": account_name, "幣種": "TWD",
            "記錄類型": rtype, "主類別": main_cat, "子類別": sub_cat,
            "金額": amount, "手續費": "", "折扣": "",
            "名稱": name, "商家": "",
            "日期": date_str, "時間": time_str,
            "專案": "", "描述": desc, "標籤": "", "對象": "",
        })
    return records


def parse_ctbc(path: Path, account_name: str) -> list[dict]:
    """解析中信 CSV（標準欄位）"""
    ensure_utf8(path)
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)

    header_idx = next(
        (i for i, r in enumerate(rows) if r and r[0].strip() == "日期"), None
    )
    if header_idx is None:
        return []

    records = []
    for row in rows[header_idx + 1 :]:
        if len(row) < 5 or not row[0].strip():
            continue
        date_str = _fmt_date(row[0].strip())
        desc     = row[1].strip()
        出_v     = _amount_int(row[2]) if len(row) > 2 else None
        入_v     = _amount_int(row[3]) if len(row) > 3 else None
        note     = row[5].strip() if len(row) > 5 else ""

        rtype             = determine_type(desc, 出_v is not None, 入_v is not None)
        main_cat, sub_cat = get_category(desc)
        amount            = -(出_v or 0) if rtype in ("支出", "轉出") else (入_v or 0)

        records.append({
            "帳戶": account_name, "幣種": "TWD",
            "記錄類型": rtype, "主類別": main_cat, "子類別": sub_cat,
            "金額": amount, "手續費": "", "折扣": "",
            "名稱": note[:30] if note else "", "商家": "",
            "日期": date_str, "時間": "09:00",
            "專案": "", "描述": desc, "標籤": "", "對象": "",
        })
    return records


# ── 檔名 → 帳戶名稱 & 解析器 ────────────────────────────
def detect_parser(path: Path):
    name = path.name
    if name.startswith("ctbc_"):
        return parse_ctbc, "中信活存"
    if "活期儲蓄" in name:
        return parse_cathay, "國泰活期儲蓄"
    if "數位存款" in name or "KOKO" in name:
        return parse_cathay, "國泰數位存款"
    return None, None
