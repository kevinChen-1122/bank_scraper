"""
bank_scraper/moze_converter/transform.py
跨帳戶轉帳配對、跨檔案去重
"""

from datetime import date as _date

from .categories import CASH_WITHDRAW_KEYWORDS


def pair_transfers(records: list[dict]) -> list[dict]:
    """
    Moze 規則：轉出/轉入必須相鄰且金額日期相同。
    這裡自動比對跨帳戶的內部轉帳（相同金額、相同日期時間、不同帳戶），
    配對成功的放相鄰；找不到配對的降級為支出/收入。
    """
    # 建立候選索引
    # out: 記錄類型==轉出，key=(abs金額, 日期, 時間)
    # in:  記錄類型 in (轉入, 收入) 且 主類別==轉帳，key=(金額, 日期, 時間)
    out_by_key: dict[tuple, int] = {}
    in_by_key:  dict[tuple, int] = {}

    for i, r in enumerate(records):
        if r["記錄類型"] == "轉出":
            key = (abs(r["金額"]), r["日期"], r["時間"])
            if key not in out_by_key:
                out_by_key[key] = i
        elif r["記錄類型"] in ("轉入", "收入") and r["主類別"] == "轉帳":
            key = (r["金額"], r["日期"], r["時間"])
            if key not in in_by_key:
                in_by_key[key] = i

    # 跨帳戶配對
    pair_dict: dict[int, int] = {}  # out_idx -> in_idx
    in_pair_set: set[int] = set()

    for key, out_idx in out_by_key.items():
        if key in in_by_key:
            in_idx = in_by_key[key]
            if records[out_idx]["帳戶"] != records[in_idx]["帳戶"]:
                pair_dict[out_idx] = in_idx
                in_pair_set.add(in_idx)

    # 重新排列：配對的相鄰輸出，未配對的轉出/轉入降級
    result = []
    for i, r in enumerate(records):
        if i in in_pair_set:
            continue  # 由配對的轉出那一行負責輸出
        rec = dict(r)
        if i in pair_dict:
            result.append(rec)  # 轉出
            in_rec = dict(records[pair_dict[i]])
            in_rec["記錄類型"] = "轉入"
            result.append(in_rec)
        else:
            if rec["記錄類型"] == "轉出":
                desc = rec.get("描述", "")
                if any(k in desc for k in CASH_WITHDRAW_KEYWORDS):
                    # 提款 → 平轉到錢包
                    result.append(rec)
                    wallet_rec = dict(rec)
                    wallet_rec["帳戶"] = "錢包"
                    wallet_rec["記錄類型"] = "轉入"
                    wallet_rec["金額"] = abs(rec["金額"])
                    result.append(wallet_rec)
                else:
                    rec["記錄類型"] = "支出"
                    result.append(rec)
            elif rec["記錄類型"] == "轉入":
                rec["記錄類型"] = "收入"
                result.append(rec)
            else:
                result.append(rec)
    return result


def deduplicate_by_latest_file(
    records_with_file_date: list[tuple[dict, "_date"]],
) -> list[dict]:
    """
    對每個 (帳戶, 交易日期) 組合，只保留來自最新 CSV 檔的記錄。
    例如 6/15 與 6/13 的 CSV 都含 6/12 的交易，則以 6/15 的為準。
    """
    latest: dict[tuple, "_date"] = {}
    for r, fd in records_with_file_date:
        key = (r["帳戶"], r["日期"])
        if fd > latest.get(key, _date.min):
            latest[key] = fd

    result = []
    removed = 0
    for r, fd in records_with_file_date:
        if fd == latest[(r["帳戶"], r["日期"])]:
            result.append(r)
        else:
            removed += 1

    if removed:
        print(f"[轉換] 🔁 以最新 CSV 覆蓋舊資料，略過 {removed} 筆")
    return result
