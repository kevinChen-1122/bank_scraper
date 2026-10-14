# bank_scraper — 銀行明細自動下載工具

自動登入中國信託、國泰世華網銀，下載交易明細 CSV，
轉換成 MOZE 匯入格式，並透過 Discord Webhook 傳送。

```
bank_scraper/
├── main.py                # 主程式進入點（排程 / 補檔都從這裡執行）
├── browser.py              # Chrome / Playwright 瀏覽器工作階段管理，兩家銀行共用：
│                           #   啟動 Chrome、崩潰狀態重設、逾時強制終止、失敗診斷截圖、
│                           #   對話框監聽（見下方「穩定性機制」）
├── scraper_ctbc.py         # 中國信託爬蟲（登入邏輯抽成 _ctbc_ensure_logged_in()，
│                           #   正式流程與 --login-only 診斷共用同一份，可單獨執行診斷）
├── scraper_cathay.py       # 國泰世華爬蟲
├── moze_converter/         # 銀行 CSV → MOZE 格式轉換（套件，拆成以下模組）
│   ├── categories.py       #   分類關鍵字表、記錄類型判斷規則
│   ├── parsers.py          #   編碼修復、中信/國泰 CSV 逐行解析
│   ├── transform.py        #   跨帳戶轉帳配對、跨檔案去重
│   ├── converter.py        #   主流程 convert_to_moze()、檔案篩選、過期清理
│   └── __main__.py         #   CLI：python -m moze_converter --date ...
├── discord_sender.py       # Discord Webhook 傳送
├── config.py                # 路徑與設定（BASE_DIR 用 Path(__file__) 動態推導）
├── keychain.py              # Mac Keychain 憑證管理
├── tests/                    # python -m unittest discover tests
│   └── test_moze_converter.py  # 金額解析／分類／轉帳配對／去重的回歸測試
├── requirements.txt
├── com.user.bankscraper.plist  # launchd 每日排程
├── chrome_profile/          # 中信用的獨立 debug Chrome profile（自動建立）
├── diagnostics/              # 兩家銀行爬取失敗時的截圖＋HTML 快照（自動建立，僅失敗時產生）
├── downloads/YYYY/MM/       # 原始銀行 CSV（自動建立，依年/月分資料夾）
├── moze/YYYY/MM/            # MOZE 格式 CSV（自動建立，依年/月分資料夾）
└── logs/                    # launchd 排程的 stdout/stderr（自動建立，main.py 會在每次執行後
                              #   自動輪替，單檔超過 5MB 就截斷只保留最近 1MB）
```

國泰爬蟲使用 Playwright 內建 Chromium 搭配獨立 persistent profile，路徑在專案資料夾之外：
`~/Library/Application Support/Google/Chrome/ScraperProfile`（見 `config.py` 的 `CATHAY_PROFILE`）。
早期版本是用系統安裝的真實 Google Chrome（`channel="chrome"`），因為系統 Chrome 在這台機器的
macOS 版本上曾連續多天隨機 segfault（崩潰位址呈現典型的「已釋放記憶體」中毒樣式
`0xcdcdcdcdcdcdcdcd`，研判是 Chrome 本身的 use-after-free bug，屬於 Playwright + Chrome/Chromium
在 macOS／Apple Silicon 上已知會發生的系統性問題，可參考
[playwright#6024](https://github.com/microsoft/playwright/issues/6024)、
[#31064](https://github.com/microsoft/playwright/issues/31064)），已試過調整穩定性 flag、
重建全新 profile、改用 headless 模式都無法解決，最後改用 Playwright 自己測試驗證過的內建
Chromium 才穩定下來。

**執行流程**

```
main.py
  ├─ 正常模式（不帶參數）：抓「昨天」一天
  │    _scrape() → scraper_ctbc.scrape_ctbc()      → downloads/YYYY/MM/ctbc_saving_YYYYMMDD.csv
  │               → scraper_cathay.scrape_cathay() → downloads/YYYY/MM/cathay_活期儲蓄存款_YYYYMMDD.csv
  │                 （國泰失敗自動以全新瀏覽器重試，        downloads/YYYY/MM/cathay_數位存款_原KOKO_YYYYMMDD.csv
  │                  最多 3 次，見下方「穩定性機制」）
  ├─ 補檔模式（--date / --from --to）：不重新爬，直接用 downloads/ 現有 CSV 轉換
  │    （銀行網站匯出的 CSV 本身就帶一段回溯區間，所以即使當天排程漏跑，
  │      之後幾天的 CSV 也常常已經涵蓋到，可以直接補轉換不必重新登入）
  ├─ moze_converter → moze/YYYY/MM/MOZE_YYYYMMDD.csv（單日）
  │                   moze/MOZE_YYYYMMDD_YYYYMMDD.csv（區間，補檔用）
  └─ discord_sender → Discord 頻道（只送 MOZE CSV 一份；若當天 0 筆則只送文字通知不附檔）
       若執行過程中有任何錯誤（stderr 非空），額外傳送錯誤摘要 + 完整執行紀錄 log_YYYYMMDD.txt
```

---

## 安裝步驟

### 1. 建立虛擬環境並安裝套件

```bash
# 建立虛擬環境（建議放在 ~/.venv/bank）
python3 -m venv ~/.venv/bank
source ~/.venv/bank/bin/activate

# 安裝套件
pip install -r requirements.txt
playwright install chromium
```

### 2. 將腳本放到家目錄

```bash
cp -r bank_scraper ~/Desktop/bank_scraper
```

`config.py` 的 `BASE_DIR` 會用 `Path(__file__)` 自動推導，搬到其他路徑不用改設定。

### 3. 儲存銀行憑證到 Mac Keychain

```bash
cd ~/Desktop/bank_scraper
source ~/.venv/bank/bin/activate

# 設定中信
python keychain.py --setup ctbc

# 設定國泰
python keychain.py --setup cathay

# 或一次設定兩家
python keychain.py --setup all
```

憑證會安全存入 Mac Keychain，**不會以明文存在任何檔案**。

需要移除時：

```bash
python keychain.py --remove ctbc
python keychain.py --remove cathay
python keychain.py --remove all
```

### 4. 設定 Discord Webhook URL

先在 Discord 頻道建立 Webhook：
頻道設定 → 整合 → Webhooks → 新 Webhook → 複製 Webhook URL

```bash
python discord_sender.py --setup
# 貼上 Webhook URL（格式：https://discord.com/api/webhooks/...）

# 測試是否正常
python discord_sender.py --test
```

---

## 首次測試執行

```bash
source ~/.venv/bank/bin/activate
cd ~/Desktop/bank_scraper
python main.py
```

兩家銀行的瀏覽器視窗都會是有頭模式（不是 headless），方便觀察登入流程。
如果只想測試/除錯中信登入，可以單獨執行：

```bash
# 只跑登入，成功後瀏覽器會保持開啟方便觀察頁面
python scraper_ctbc.py --login-only
```

### 網銀頁面若改版

兩家銀行的 DOM selector 都寫在 `scraper_ctbc.py` / `scraper_cathay.py` 裡，
沒有集中標記，需要的話直接對照瀏覽器 DevTools 找出對應元素修改。

常用除錯方式：
```bash
PWDEBUG=1 python main.py
```

### 穩定性機制（`browser.py`，兩家銀行共用）

國泰爬蟲用的 Chrome/Chromium 在自動化情境下不時會出現瀏覽器層級的問題（崩潰、卡死），
跟網銀網站或程式邏輯無關，`browser.py` 因此內建以下幾層保護：

- **崩潰狀態重設**：若上次執行意外中斷，Chrome 會把 profile 標記為「異常結束」，
  下次啟動觸發的復原流程若剛好也有問題，會在同一位置重播同一個崩潰。啟動前主動
  重設這個標記，避免每天卡在同一個地方。
- **逾時強制終止**：單次嘗試超過 90 秒沒完成就視為卡死，強制放棄；若連正常關閉
  瀏覽器都逾時，直接在系統層級 `pkill -KILL` 該 profile 底下所有程序，確保下次
  重試不會被卡住的舊程序拖累（`SCRAPE_HANG_TIMEOUT`）。
- **失敗自動重試**：`main.py` 對國泰的整體流程（登入到下載）最多重試 3 次，中間
  換一個全新的瀏覽器實例。
- **失敗診斷截圖**：關鍵步驟失敗時自動把當下畫面截圖＋完整 HTML 存到 `diagnostics/`，
  事後可以直接打開截圖確認當時網頁實際長怎樣，不用等下次剛好碰到才能觀察。
  中信的登入流程另外內建「偵測既有 session 直接跳過登入」與「逾時重新導向登入頁
  重試一次」的容錯，並會記錄頁面跳出的 alert/confirm 對話框內容到 stderr。

**注意**：`diagnostics/` 裡的截圖可能包含帳戶餘額等真實資料，跟 `downloads/` 的銀行
CSV 一樣只受本機安全性保護（見下方 FileVault 的提醒）。

---

## 設定每日 12:00 自動執行

### 1. 確認 plist 中的路徑

`com.user.bankscraper.plist` 裡的 `ProgramArguments` 需指向實際的虛擬環境 python
（例如 `/Users/你的帳號/.venv/bank/bin/python`）與 `main.py` 的絕對路徑，兩者都要用絕對路徑，不支援 `~`。

```bash
whoami  # 確認使用者名稱，用來核對/修改路徑
```

### 2. 建立 log 目錄並安裝排程

```bash
mkdir -p ~/Desktop/bank_scraper/logs

cp ~/Desktop/bank_scraper/com.user.bankscraper.plist \
   ~/Library/LaunchAgents/

launchctl load ~/Library/LaunchAgents/com.user.bankscraper.plist
```

### 3. 驗證排程已載入

```bash
launchctl list | grep bankscraper
```

### 4. 手動觸發測試（不用等到 12 點）

```bash
launchctl start com.user.bankscraper
```

---

## 補檔（排程失敗或漏跑時）

`main.py` 支援三種執行方式：

```bash
python main.py                                   # 正常排程：抓「昨天」
python main.py --date 2026-06-10                  # 補單天：用現有 downloads/ CSV 轉換+傳送，不重新爬
python main.py --from 2026-06-01 --to 2026-06-10  # 補區間：同上，範圍模式
```

補檔模式**不會重新登入銀行網站**，只會用 `downloads/` 裡已經下載的 CSV 做轉換。
如果缺的那幾天完全沒有任何 CSV涵蓋到（例如缺口在下載紀錄最前面、或超過銀行網站保留的回溯區間），
才需要用 `python scraper_ctbc.py` / 或跑一次完整 `python main.py`（會抓當下「昨天」）來實際重新爬取。

只想重新產生 MOZE CSV、不需要順便傳 Discord 的話，也可以直接呼叫轉換套件：

```bash
python -m moze_converter --date 2026-06-10
```

---

## 下載與輸出的檔案位置

```
~/Desktop/bank_scraper/downloads/2026/08/     ← 原始銀行 CSV
├── ctbc_saving_20260809.csv
├── cathay_活期儲蓄存款_20260809.csv
└── cathay_數位存款_原KOKO_20260809.csv

~/Desktop/bank_scraper/moze/2026/08/          ← MOZE 匯入用 CSV
└── MOZE_20260809.csv
```

Discord 頻道**只會收到 MOZE CSV 一份**（不會上傳三份原始銀行 CSV）；
當天若無交易，只送文字通知、不附檔案；若執行過程有錯誤，另外會收到錯誤摘要與完整 log 檔。

---

## 移除排程

```bash
launchctl unload ~/Library/LaunchAgents/com.user.bankscraper.plist
```

---

## 已知限制 / TODO

- **僅支援 macOS**：`keychain.py` 是直接呼叫 macOS 內建的 `security` CLI 讀寫 Keychain，
  在 Windows 或 Docker 容器（含跑在 Mac 上的 Docker Desktop，容器本身是獨立 Linux 環境）
  完全無法運作，會直接報 `command not found`。
  - TODO：若要跨平台，需改用 [`keyring`](https://pypi.org/project/keyring/) 套件
    （自動支援 macOS Keychain / Windows Credential Manager / Linux Secret Service）取代目前的 `security` 呼叫。
  - Docker 容器內沒有登入 session、沒有 Secret Service，OS 層級加密儲存沒有直接對應方案，
    需改成執行時從外部注入（環境變數、掛載唯讀 secret 檔、或串接 Vault 等 secrets manager）。
- 這台機器目前 **FileVault 未開啟**：Keychain 內的密碼本身有獨立加密保護，
  但磁碟未加密代表若電腦/硬碟實體遺失，`downloads/` 的明碼交易紀錄與 `chrome_profile/` /
  `ScraperProfile` 裡可能還沒過期的網銀登入 session cookie 會直接可讀，等同繞過密碼直接進入網銀。
  建議之後評估開啟 FileVault。
