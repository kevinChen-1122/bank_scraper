"""
bank_scraper/tests/test_moze_converter.py
針對 moze_converter 套件裡沒有 I/O 的純邏輯做回歸測試：
金額解析、分類判斷、轉帳配對、去重。
這些規則藏在解析器裡不明顯（例如「費用沖正的負數出帳要反轉成入帳」），
之前沒有任何測試保護，改壞了也不會有任何錯誤訊息，只會在轉出去的 MOZE
資料裡悄悄多一筆或少一筆。

執行：
  python -m unittest discover tests
"""

import unittest
from datetime import date

from moze_converter.categories import determine_type, get_category
from moze_converter.parsers import _amount_int
from moze_converter.transform import deduplicate_by_latest_file, pair_transfers


class TestAmountInt(unittest.TestCase):
    def test_parses_thousands_separator(self):
        self.assertEqual(_amount_int("15,000"), 15000)

    def test_parses_fullwidth_thousands_separator(self):
        self.assertEqual(_amount_int("1，200"), 1200)

    def test_empty_dash_is_none(self):
        for s in ("−", "-", "", "－"):
            self.assertIsNone(_amount_int(s))

    def test_decimal_truncates(self):
        self.assertEqual(_amount_int("1234.0"), 1234)


class TestCategoryAndType(unittest.TestCase):
    def test_known_keyword_maps_to_category(self):
        self.assertEqual(get_category("網銀轉帳"), ("轉帳", "轉帳"))
        self.assertEqual(get_category("簽帳消費 星巴克"), ("消費", "簽帳"))

    def test_unknown_desc_falls_back_to_other(self):
        desc = "完全沒見過的描述文字內容超過十字"
        cat, sub = get_category(desc)
        self.assertEqual(cat, "其他")
        self.assertEqual(sub, desc[:10])

    def test_income_keyword_with_in_amount_is_income(self):
        self.assertEqual(determine_type("薪資", has_out=False, has_in=True), "收入")

    def test_transfer_in_keyword_is_transfer_in(self):
        self.assertEqual(determine_type("跨行轉入", has_out=False, has_in=True), "轉入")

    def test_transfer_out_keyword_is_transfer_out(self):
        self.assertEqual(determine_type("網銀轉帳", has_out=True, has_in=False), "轉出")

    def test_plain_outflow_is_expense(self):
        self.assertEqual(determine_type("超商消費", has_out=True, has_in=False), "支出")

    def test_plain_inflow_is_income(self):
        self.assertEqual(determine_type("不明入帳", has_out=False, has_in=True), "收入")


def _rec(帳戶, 記錄類型, 主類別="轉帳", 金額=0, 日期="2026/8/9", 時間="09:00", 描述=""):
    return {
        "帳戶": 帳戶, "幣種": "TWD", "記錄類型": 記錄類型, "主類別": 主類別, "子類別": "轉帳",
        "金額": 金額, "手續費": "", "折扣": "", "名稱": "", "商家": "",
        "日期": 日期, "時間": 時間, "專案": "", "描述": 描述, "標籤": "", "對象": "",
    }


class TestPairTransfers(unittest.TestCase):
    def test_cross_account_transfer_is_paired_adjacent(self):
        records = [
            _rec("中信活存", "轉出", 金額=-5000, 描述="網銀轉帳"),
            _rec("國泰活期儲蓄", "轉入", 金額=5000, 描述="電子轉帳"),
        ]
        result = pair_transfers(records)
        self.assertEqual([r["記錄類型"] for r in result], ["轉出", "轉入"])
        self.assertEqual(result[0]["帳戶"], "中信活存")
        self.assertEqual(result[1]["帳戶"], "國泰活期儲蓄")

    def test_unpaired_cash_withdrawal_creates_wallet_leg(self):
        records = [_rec("中信活存", "轉出", 金額=-2000, 描述="自行提款")]
        result = pair_transfers(records)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["記錄類型"], "轉出")
        self.assertEqual(result[1]["帳戶"], "錢包")
        self.assertEqual(result[1]["記錄類型"], "轉入")
        self.assertEqual(result[1]["金額"], 2000)

    def test_unpaired_non_cash_outflow_downgrades_to_expense(self):
        records = [_rec("中信活存", "轉出", 金額=-800, 描述="跨行費用")]
        result = pair_transfers(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["記錄類型"], "支出")

    def test_unpaired_inflow_downgrades_to_income(self):
        records = [_rec("國泰活期儲蓄", "轉入", 金額=3000, 描述="電子轉帳")]
        result = pair_transfers(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["記錄類型"], "收入")

    def test_same_account_transfer_is_not_paired(self):
        # 同帳戶的轉出/轉入不算內部轉帳，應各自降級
        records = [
            _rec("中信活存", "轉出", 金額=-1000, 描述="網銀轉帳"),
            _rec("中信活存", "轉入", 金額=1000, 描述="電子轉帳"),
        ]
        result = pair_transfers(records)
        self.assertEqual([r["記錄類型"] for r in result], ["支出", "收入"])


class TestDeduplicateByLatestFile(unittest.TestCase):
    def test_keeps_record_from_latest_file_only(self):
        old_record = _rec("中信活存", "支出", 金額=-100, 日期="2026/8/1")
        new_record = _rec("中信活存", "支出", 金額=-999, 日期="2026/8/1")
        result = deduplicate_by_latest_file([
            (old_record, date(2026, 8, 1)),
            (new_record, date(2026, 8, 3)),
        ])
        self.assertEqual(result, [new_record])

    def test_different_accounts_or_dates_are_independent(self):
        a = _rec("中信活存", "支出", 金額=-100, 日期="2026/8/1")
        b = _rec("國泰活期儲蓄", "支出", 金額=-200, 日期="2026/8/1")
        result = deduplicate_by_latest_file([
            (a, date(2026, 8, 1)),
            (b, date(2026, 8, 1)),
        ])
        self.assertEqual(result, [a, b])


if __name__ == "__main__":
    unittest.main()
