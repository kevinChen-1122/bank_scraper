"""
bank_scraper/scraper_cathay.py
國泰世華網銀爬蟲：下載活儲＋數位存款(KOKO) 昨日明細
"""

import sys
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR, CATHAY_ACCOUNTS
from keychain import get_credentials


async def scrape_cathay(page, yesterday: datetime) -> list[Path]:
    """登入國泰世華，下載活儲＋數位存款(KOKO) 明細，回傳下載檔案路徑清單"""
    creds    = get_credentials("cathay")
    date_str = yesterday.strftime("%Y%m%d")
    downloaded: list[Path] = []

    print("[國泰] 前往登入頁...")
    await page.goto("https://www.cathaybk.com.tw/MyBank/", wait_until="networkidle")

    # persistent profile 若殘留前次未正常登出的 session，會跳出「貼心提醒」
    # 彈窗擋住登入表單，需先點擊彈窗內的登出才能繼續
    relogin_modal = page.locator("#divReloginConfirm")
    if await relogin_modal.count() and await relogin_modal.is_visible():
        print("[國泰] 偵測到重複登入提醒彈窗，點擊登出以繼續")
        await relogin_modal.get_by_text("登出", exact=True).click()
        await page.wait_for_timeout(1500)

    for _ in range(5):
        clicked = await page.evaluate("""
            (() => {
                const btns = Array.from(document.querySelectorAll('button'))
                    .filter(el => el.offsetParent !== null);
                const next = btns.find(el => el.innerText.trim() === '下一則');
                const ok   = btns.find(el => el.innerText.trim() === '我知道了');
                if (next) { next.click(); return 'next'; }
                if (ok)   { ok.click();   return 'ok'; }
                return 'none';
            })()
        """)
        if clicked == 'none':
            break
        await page.wait_for_timeout(600)

    await page.wait_for_selector('#CustID', timeout=5000)
    await page.fill('#CustID',        creds["id_number"])
    await page.fill('#UserIdKeyin',   creds["username"])
    await page.fill('#PasswordKeyin', creds["password"])
    await page.click('button.js-login')
    await page.wait_for_load_state("networkidle")
    print("[國泰] 登入完成")

    await page.goto(
        "https://www.cathaybk.com.tw/OnlineBanking/AcctInq/B0103_TxnDtlInq",
        wait_until="networkidle",
    )
    await page.wait_for_timeout(1000)

    is_collapsed = await page.evaluate("""
        () => !!Array.from(document.querySelectorAll('div.css-okqwuj'))
            .find(el => el.offsetParent !== null && el.innerText?.trim().startsWith('展開'))
    """)
    if is_collapsed:
        await page.locator('div.css-okqwuj').filter(has_text='展開').first.click()
        await page.wait_for_timeout(500)

    for acct in CATHAY_ACCOUNTS:
        print(f"[國泰] 處理帳戶：{acct['label']}")

        # 展開帳戶下拉選單：用 Playwright 原生 focus + 鍵盤事件（透過 CDP
        # 送出真正的輸入），比用 JS dispatchEvent() 偽造合成鍵盤事件更貼近
        # 真實使用者操作，react-select 這類元件對合成事件的相容性也較不穩定。
        account_combobox = page.locator(
            '[aria-describedby^="react-select-2"], '
            '[id^="react-select-2"][class*="dummy"], '
            '[role="combobox"]'
        ).first
        await account_combobox.focus()
        await page.keyboard.press("Space")
        await page.wait_for_timeout(500)
        option_id = f"react-select-2-option-{acct['option_index']}"
        await page.evaluate(f"document.querySelector('#{option_id}')?.click()")
        await page.wait_for_timeout(500)

        date_range_combobox = page.locator('[aria-describedby^="react-select-3"]')
        await date_range_combobox.focus()
        await page.keyboard.press("Space")
        await page.wait_for_timeout(500)
        await page.evaluate("document.querySelector('#react-select-3-option-1')?.click()")
        await page.wait_for_timeout(400)

        await page.evaluate("""
            (() => {
                const btns = Array.from(document.querySelectorAll('button'))
                    .filter(el => el.offsetParent !== null && el.innerText.trim() === '查詢');
                btns[btns.length - 1]?.click();
            })()
        """)
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(800)

        output_path = OUTPUT_DIR / yesterday.strftime("%Y") / yesterday.strftime("%m") / f"cathay_{acct['label']}_{date_str}.csv"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await page.evaluate("""
            (() => {
                const btns = Array.from(document.querySelectorAll('button'))
                    .filter(el => el.offsetParent !== null && el.innerText.trim().includes('列印'));
                btns[0]?.click();
            })()
        """)
        await page.wait_for_timeout(400)
        async with page.expect_download() as dl_info:
            await page.evaluate("document.querySelector('button[data-evt=\"btn_csv_download\"]')?.click()")
        download = await dl_info.value
        await download.save_as(output_path)
        downloaded.append(output_path)
        print(f"[國泰] 已下載：{output_path}")

        await page.evaluate("""
            (() => {
                const btns = Array.from(document.querySelectorAll('button'))
                    .filter(el => el.offsetParent !== null && el.innerText.trim() === '清除條件');
                btns[0]?.click();
            })()
        """)
        await page.wait_for_timeout(400)

    await page.evaluate("""
        (() => {
            const btns = Array.from(document.querySelectorAll('button'))
                .filter(el => el.offsetParent !== null && el.innerText.trim() === '登出');
            btns[0]?.click();
        })()
    """)
    await page.wait_for_timeout(1000)
    for _ in range(3):
        clicked = await page.evaluate("""
            (() => {
                const btns = Array.from(document.querySelectorAll('button'))
                    .filter(el => el.offsetParent !== null);
                const ok = btns.find(el =>
                    ['我知道了','確定','關閉'].includes(el.innerText.trim())
                );
                if (ok) { ok.click(); return 'ok'; }
                return 'none';
            })()
        """)
        if clicked == 'none':
            break
        await page.wait_for_timeout(500)

    return downloaded
