"""
bank_scraper/discord_sender.py
透過 Discord Webhook 傳送檔案與摘要訊息

Webhook URL 從 Mac Keychain 讀取（service: discord, account: webhook_url）
初次設定：python discord_sender.py --setup
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

import requests

from keychain import keychain_get, keychain_set

KEYCHAIN_SERVICE = "discord"
KEYCHAIN_ACCOUNT = "webhook_url"

# Discord 單檔上傳上限（8 MB，一般伺服器）
MAX_FILE_BYTES = 8 * 1024 * 1024


# ── Keychain 存取 ───────────────────────────────────────────
def get_webhook_url() -> str:
    return keychain_get(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)


def setup_webhook_url(url: str | None = None):
    """儲存 Webhook URL 到 Keychain；未提供則互動式輸入"""
    if url is None:
        url = input("請貼上 Discord Webhook URL：").strip()
    else:
        url = url.strip()
    if not url.startswith("https://discord.com/api/webhooks/"):
        print("⚠️ 格式不對，應為 https://discord.com/api/webhooks/... 開頭")
        sys.exit(1)
    keychain_set(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, url)
    print("✅ Webhook URL 已儲存至 Mac Keychain")


# ── 傳送邏輯 ────────────────────────────────────────────────
def _build_summary_embed(
    files: list[Path],
    date_str: str,
    backfill: bool = False,
    count: int | None = None,
) -> dict:
    """建立 Discord Embed 摘要卡片"""
    if files:
        lines = [f"📄 `{f.name}` ({f.stat().st_size / 1024:.1f} KB)" for f in files]
        description = "\n".join(lines)
    else:
        description = "當日無交易記錄"

    count_str = f" · {count} 筆" if count is not None else ""
    title = f"🔄 補檔 {date_str}{count_str}" if backfill else f"💰 {date_str}{count_str}"
    return {
        "title": title,
        "description": description,
        "color": 0x5865F2,  # Discord blurple
        "footer": {"text": "bank_scraper · MOZE 匯入用"},
        "timestamp": datetime.utcnow().isoformat(),
    }


def send_files(
    files: list[Path],
    webhook_url: str | None = None,
    message: str = "",
    label: str | None = None,
    backfill: bool = False,
    count: int | None = None,
) -> bool:
    """
    上傳檔案清單到 Discord Webhook。
    先傳一則含摘要 Embed 的訊息，再逐檔附上檔案。

    Args:
        files:       要傳送的 Path 清單
        webhook_url: 若為 None 則從 Keychain 讀取
        message:     額外的文字訊息（選填）
        label:       顯示用的日期標籤（補檔時傳入對應區間）
        backfill:    是否為補檔模式（影響標題與訊息文字）

    Returns:
        True = 全部成功，False = 有任何失敗
    """
    url = webhook_url or get_webhook_url()
    date_str = label or datetime.now().strftime("%Y/%m/%d")
    ok = True

    # ── 1. 先傳摘要 Embed ──────────────────────────────────
    embed = _build_summary_embed(files, date_str, backfill=backfill, count=count)
    count_str = f"，共 {count} 筆" if count is not None else ""
    default_content = (
        f"**{date_str}** 補檔完成{count_str}"
        if backfill else
        f"**{date_str}** 交易記錄{count_str}"
    )
    payload = {
        "content": message or default_content,
        "embeds": [embed],
    }
    resp = requests.post(url, json=payload, timeout=15)
    if resp.status_code not in (200, 204):
        print(f"[Discord] ❌ 傳送摘要失敗：{resp.status_code} {resp.text}", file=sys.stderr)
        ok = False
    else:
        print("[Discord] ✅ 摘要已傳送")

    # ── 2. 逐檔上傳 ───────────────────────────────────────
    for path in files:
        if not path.exists():
            print(f"[Discord] ⚠️ 檔案不存在，略過：{path}", file=sys.stderr)
            continue

        file_size = path.stat().st_size
        if file_size > MAX_FILE_BYTES:
            print(f"[Discord] ⚠️ 檔案超過 8 MB，略過：{path.name}", file=sys.stderr)
            continue

        with open(path, "rb") as f:
            resp = requests.post(
                url,
                files={"file": (path.name, f, "text/csv")},
                timeout=30,
            )
        if resp.status_code in (200, 204):
            print(f"[Discord] ✅ 已上傳：{path.name}")
        else:
            print(f"[Discord] ❌ 上傳失敗：{path.name} → {resp.status_code} {resp.text}", file=sys.stderr)
            ok = False

    return ok


def send_error_report(
    errors: str,
    webhook_url: str | None = None,
    full_log: str | None = None,
) -> bool:
    """
    將執行期間收集到的錯誤訊息傳送至 Discord。
    通常在 main() 結束時呼叫，只有 errors 非空才實際送出。

    Args:
        errors:   stderr 收集到的錯誤文字
        full_log: 完整 stdout 執行記錄，若提供則以附件方式補充上傳
    """
    if not errors.strip():
        return True

    url      = webhook_url or get_webhook_url()
    date_str = datetime.now().strftime("%Y/%m/%d")
    ok       = True

    # ── 1. 送錯誤 Embed ────────────────────────────────────
    payload = {
        "content": f"⚠️ **{date_str} bank_scraper 執行錯誤**",
        "embeds": [{
            "description": f"```\n{errors[:3900]}\n```",
            "color": 0xED4245,
        }],
    }
    resp = requests.post(url, json=payload, timeout=15)
    if resp.status_code in (200, 204):
        print("[Discord] ✅ 錯誤報告已傳送")
    else:
        print(f"[Discord] ❌ 錯誤報告傳送失敗：{resp.status_code}", file=sys.stderr)
        ok = False

    # ── 2. 附上完整執行記錄 ────────────────────────────────
    if full_log:
        filename = f"log_{date_str.replace('/', '')}.txt"
        resp = requests.post(
            url,
            files={"file": (filename, full_log.encode("utf-8"), "text/plain")},
            timeout=30,
        )
        if resp.status_code in (200, 204):
            print(f"[Discord] ✅ 完整執行記錄已上傳：{filename}")
        else:
            print(f"[Discord] ❌ 執行記錄上傳失敗：{resp.status_code}", file=sys.stderr)
            ok = False

    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discord Webhook 工具")
    parser.add_argument("--setup", nargs="?", const="__prompt__",
                        metavar="WEBHOOK_URL",
                        help="設定 Webhook URL 到 Keychain（可直接帶 URL，或不帶則互動輸入）")
    parser.add_argument("--test",  action="store_true", help="傳送測試訊息確認 Webhook 是否正常")
    args = parser.parse_args()

    if args.setup is not None:
        setup_webhook_url(None if args.setup == "__prompt__" else args.setup)
    elif args.test:
        url = get_webhook_url()
        resp = requests.post(url, json={"content": "✅ bank_scraper webhook 測試訊息"}, timeout=10)
        if resp.status_code in (200, 204):
            print("✅ Webhook 測試成功")
        else:
            print(f"❌ Webhook 測試失敗：{resp.status_code} {resp.text}")
    else:
        parser.print_help()