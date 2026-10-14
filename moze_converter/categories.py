"""
bank_scraper/moze_converter/categories.py
交易說明 → MOZE 分類 / 記錄類型的判斷規則
"""

# ── 說明文字 → (主類別, 子類別) ─────────────────────────
CATEGORY_MAP = {
    "網銀轉帳":   ("轉帳", "轉帳"),
    "跨行轉入":   ("轉帳", "轉帳"),
    "跨行轉出":   ("轉帳", "轉帳"),
    "電子轉帳":   ("轉帳", "轉帳"),
    "電子預約":   ("轉帳", "轉帳"),
    "電子轉出":   ("轉帳", "轉帳"),
    "ＡＴＭ存":   ("轉帳", "轉帳"),
    "匯入匯款":   ("轉帳", "轉帳"),
    "匯出匯款":   ("轉帳", "轉帳"),
    "跨行轉":     ("轉帳", "轉帳"),
    "跨行費用":   ("手續費", "跨行手續費"),
    "信用卡款":   ("信用卡", "信用卡還款"),
    "簽帳消費":   ("消費", "簽帳"),
    "卡片費用":   ("手續費", "國外交易手續費"),
    "繳費":       ("帳單", "繳費"),
    "無卡提款":   ("現金", "提款"),
    "自行提款":   ("現金", "提款"),
    "跨行提款":   ("現金", "提款"),
    "現金":       ("現金", "提款"),
    "綜所稅":     ("稅費", "綜所稅"),
    "薪資":       ("收入", "薪資"),
    "存款息":     ("收入", "利息"),
    "轉入代發":   ("收入", "代發"),
    "費用沖正":   ("收入", "退款"),
    "錯誤更正":   ("收入", "退款"),
    "轉帳提":     ("轉帳", "轉帳"),
    "轉帳":       ("轉帳", "轉帳"),   # 放最後避免提早匹配含「轉帳」的其他描述
}

# ── 記錄類型判斷 ─────────────────────────────────────────
TRANSFER_IN_KEYWORDS  = {"跨行轉入", "電子轉帳", "ＡＴＭ存", "薪資", "匯入匯款", "轉入代發"}
TRANSFER_OUT_KEYWORDS = {"網銀轉帳", "電子預約", "跨行轉帳", "跨行轉", "電子轉出", "匯出匯款",
                         "無卡提款", "自行提款", "跨行提款", "現金"}
INCOME_KEYWORDS       = {"薪資", "存款息", "轉入代發"}

# 提款關鍵字：轉出後自動配對一筆「錢包」轉入
CASH_WITHDRAW_KEYWORDS = {"無卡提款", "自行提款", "跨行提款", "現金"}


def get_category(desc: str) -> tuple[str, str]:
    desc = desc.strip()
    for key, val in CATEGORY_MAP.items():
        if key in desc:
            return val
    return ("其他", desc[:10] if desc else "其他")


def determine_type(desc: str, has_out: bool, has_in: bool) -> str:
    """判斷 MOZE 記錄類型"""
    desc = desc.strip()
    # 明確收入
    if any(k in desc for k in INCOME_KEYWORDS) and has_in:
        return "收入"
    # 轉帳類
    if any(k in desc for k in TRANSFER_IN_KEYWORDS) and has_in:
        return "轉入"
    if any(k in desc for k in TRANSFER_OUT_KEYWORDS) and has_out:
        return "轉出"
    # 一般
    if has_out:
        return "支出"
    if has_in:
        return "收入"
    return "支出"
