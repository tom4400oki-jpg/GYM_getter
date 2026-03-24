"""等々力テニスコートの施設一覧を確認"""
import asyncio, os, sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

async def explore():
    from backend.scrapers.fureai_net import FureaiNetScraper
    async with FureaiNetScraper(headless=True,
            user_id=os.getenv("FUREAI_USER_ID",""),
            password=os.getenv("FUREAI_PASSWORD","")) as s:
        await s.login()
        # 予約: 等々力テニスコート
        await s._click_menu_link("予約の申込み")
        await s._click_button("地域から")
        await s._click_link_text("中原区")
        await s._click_link_text("等々力テニスコート")
        print("URL:", s.page.url)
        inst_links = await s.page.evaluate("""
            () => [...document.querySelectorAll('a')].filter(a => a.href.includes('sendInstNo'))
                .map(a => ({text: a.innerText.trim().replace(/\\n/g,' '), href: a.href.substring(0,90)}))
        """)
        print("等々力テニスコート施設:")
        for l in inst_links: print(f"  {l['text']!r:40} -> {l['href']}")

        # 抽選: 等々力テニスコート
        print("\n抽選: 等々力テニスコート")
        await s._click_menu_link("抽選の申込み")
        print("抽選メニューURL:", s.page.url)
        await s._click_button("地域から")
        await s._click_link_text("中原区")
        body = await s.page.inner_text("body")
        # テニス探す
        for line in body.splitlines():
            if "テニス" in line or "\u7b49\u3005\u529b" in line:
                print(f"  {line.strip()}")
        await s._click_link_text("等々力テニスコート")
        inst_links = await s.page.evaluate("""
            () => [...document.querySelectorAll('a')].filter(a => a.href.includes('sendInstNo'))
                .map(a => ({text: a.innerText.trim().replace(/\\n/g,' '), href: a.href.substring(0,90)}))
        """)
        print("抽選 等々力テニスコート施設:")
        for l in inst_links: print(f"  {l['text']!r:40} -> {l['href']}")

asyncio.run(explore())
