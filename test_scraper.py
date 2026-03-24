import asyncio
import sys
sys.path.insert(0, '.')

async def test():
    from backend.scrapers.fureai_net import FureaiNetScraper
    try:
        async with FureaiNetScraper(headless=True) as scraper:
            print("Playwright OK, navigating...")
            await scraper.page.goto(
                'https://www.fureai-net.city.kawasaki.jp/web/',
                wait_until='networkidle',
                timeout=30000
            )
            print('Page OK:', scraper.page.url)
    except Exception as e:
        print(f'ERROR: {type(e).__name__}: {e}')

asyncio.run(test())
