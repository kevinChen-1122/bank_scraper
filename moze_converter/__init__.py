"""
bank_scraper/moze_converter/
將銀行匯出的 CSV 轉換成 MOZE 匯入格式

支援：
  - 中國信託  ctbc_saving_YYYYMMDD.csv       (Big5, CRLF)
  - 國泰世華  cathay_活期儲蓄存款_YYYYMMDD.csv  (UTF-8 BOM, 多行欄位)
  - 國泰世華  cathay_數位存款_原KOKO_YYYYMMDD.csv

對外只需要這三個函式（main.py 用的就是這幾個）：
  convert_to_moze / select_relevant_files / cleanup_old_files
"""

from .converter import cleanup_old_files, convert_to_moze, select_relevant_files

__all__ = [
    "convert_to_moze",
    "select_relevant_files",
    "cleanup_old_files",
]
