"""メッセージ確認ページの突破方法を調査"""
import asyncio, os, sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

async def main():
    from backend.scrapers.fureai_net import FureaiNetScraper
    async with FureaiNetScraper(headless=False,  # 見えるモードで確認
            user_id=os.getenv("FUREAI_USER_ID",""),
            password=os.getenv("FUREAI_PASSWORD","")) as s:
        await s.login()
        print("URL:", s.page.url)
        print("Title:", await s.page.title())

        # 全フォーム要素
        fields = await s.get_form_fields()
        print("\n全フォーム要素:")
        for f in fields:
            if f['type'] != 'hidden':
                print(f"  {f['type']:10} name={f['name']!r:20} value={f['value']!r}")

        # hidden fieldsも確認
        print("\nhidden fields:")
        for f in fields:
            if f['type'] == 'hidden':
                print(f"  name={f['name']!r:25} value={f['value'][:60]!r}")

        # ページHTML一部
        html = await s.page.content()
        print("\nHTML (2000字):")
        print(html[:2000])

asyncio.run(main())
