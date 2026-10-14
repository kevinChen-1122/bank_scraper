"""
bank_scraper/config.py
路徑與設定集中管理
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ── 目錄 ─────────────────────────────────────────────────────
OUTPUT_DIR = BASE_DIR / "downloads"   # 存放銀行匯出的原始 CSV
MOZE_DIR   = BASE_DIR / "moze"        # 轉換後的 MOZE CSV
LOGS_DIR   = BASE_DIR / "logs"        # launchd 的 stdout/stderr（見 com.user.bankscraper.plist）

# ── Chrome 設定 ──────────────────────────────────────────────
CHROME_BIN        = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_PROFILE    = BASE_DIR / "chrome_profile"   # CTBC 獨立 profile
CHROME_DEBUG_PORT = 9222

# 國泰使用 persistent context（與日常 Chrome 不同的獨立 profile）
CATHAY_PROFILE = Path.home() / "Library/Application Support/Google/Chrome/ScraperProfile"

# ── 檔案保留設定 ─────────────────────────────────────────────
FILES_KEEP_YEARS = 1   # 保留最近幾年的下載與輸出檔案

# ── 各銀行設定 ───────────────────────────────────────────────
# 帳戶選項 index（對應 react-select-2-option-N）
# 0: 003506180405 活期儲蓄存款
# 1: 699505561574 數位存款帳戶２類
# 2: 699508456321 數位存款帳戶２類 (原KOKO)
CATHAY_ACCOUNTS = [
    {"label": "活期儲蓄存款",    "option_index": 0},
    {"label": "數位存款_原KOKO", "option_index": 2},
]
