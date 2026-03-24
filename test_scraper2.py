"""トップページの構造確認"""
import asyncio, sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

async def test():
    from backend.scrapers.fureai_net import FureaiNetScraper, TOP_URL

    async with FureaiNetScraper(headless=False) as scraper:  # headless=False で目視確認
        print("=== トップページへ移動 ===")
        await scraper.page.goto(TOP_URL, wait_until="networkidle", timeout=30000)
        print("URL:", scraper.page.url)

        links = await scraper.get_nav_links()
        print(f"\nリンク ({len(links)}件):")
        for l in links:
            print(f"  {l['text']!r} -> {l['href']!r}")

        fields = await scraper.get_form_fields()
        print(f"\nフォーム要素 ({len(fields)}件):")
        for f in fields:
            print(f"  {f['tag']} name={f['name']!r} type={f['type']!r}")

        input("Enterキーで終了...")

asyncio.run(test())
