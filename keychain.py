"""
bank_scraper/keychain.py
從 Mac Keychain 讀取銀行登入憑證（安全，不明文存檔）

初次設定：python keychain.py --setup ctbc | cathay | all
"""

import argparse
import getpass
import subprocess


def keychain_get(service: str, account: str) -> str:
    """從 Keychain 讀取單一欄位"""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Keychain 找不到 service='{service}', account='{account}'。\n"
            f"請先執行：python keychain.py --setup {service}"
        )
    return result.stdout.strip()


def keychain_set(service: str, account: str, password: str):
    """寫入或更新 Keychain"""
    subprocess.run(
        ["security", "delete-generic-password", "-s", service, "-a", account],
        capture_output=True,
    )
    result = subprocess.run(
        ["security", "add-generic-password", "-s", service, "-a", account, "-w", password],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"寫入 Keychain 失敗：{result.stderr}")


def keychain_delete(service: str, account: str) -> bool:
    """從 Keychain 移除單一欄位，回傳是否有找到並刪除"""
    result = subprocess.run(
        ["security", "delete-generic-password", "-s", service, "-a", account],
        capture_output=True,
    )
    return result.returncode == 0


def get_credentials(bank: str) -> dict:
    """讀取指定銀行的憑證，回傳 {"id_number", "username", "password"}"""
    return {
        "id_number": keychain_get(bank, "id_number"),
        "username":  keychain_get(bank, "username"),
        "password":  keychain_get(bank, "password"),
    }


def setup_credentials(bank: str):
    """互動式設定，將憑證存入 Keychain"""
    print(f"\n=== 設定 {bank.upper()} 銀行憑證（輸入內容不會顯示）===")
    id_number = input("身分證字號：").strip()
    username  = input("使用者代號：").strip()
    password  = getpass.getpass("網銀密碼：")

    keychain_set(bank, "id_number", id_number)
    keychain_set(bank, "username",  username)
    keychain_set(bank, "password",  password)
    print(f"✅ {bank.upper()} 憑證已安全儲存至 Mac Keychain")


def remove_credentials(bank: str):
    """從 Keychain 移除指定銀行的所有憑證欄位"""
    found_any = False
    for account in ("id_number", "username", "password"):
        if keychain_delete(bank, account):
            found_any = True
    if found_any:
        print(f"✅ {bank.upper()} 憑證已從 Mac Keychain 移除")
    else:
        print(f"⚠️ 沒有找到 {bank.upper()} 的憑證（可能本來就沒設定過）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", choices=["ctbc", "cathay", "all"],
                        help="初次設定銀行憑證")
    parser.add_argument("--remove", choices=["ctbc", "cathay", "all"],
                        help="從 Keychain 移除銀行憑證")
    args = parser.parse_args()

    if args.setup and args.remove:
        parser.error("--setup 不能與 --remove 同時使用")

    if args.setup == "all":
        setup_credentials("ctbc")
        setup_credentials("cathay")
    elif args.setup:
        setup_credentials(args.setup)
    elif args.remove == "all":
        remove_credentials("ctbc")
        remove_credentials("cathay")
    elif args.remove:
        remove_credentials(args.remove)
    else:
        parser.print_help()
