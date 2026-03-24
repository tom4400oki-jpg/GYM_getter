"""空き確認テスト"""
import asyncio
import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()


async def test():
    from backend.scrapers.fureai_net import FureaiNetScraper, get_target_dates

    facility = {"id": "takatsu_sports_center", "name": "高津スポーツセンター"}
    # 今日から45日分
    target_dates = get_target_dates(45)
    print(f"対象日: {target_dates[0]} ～ {target_dates[-1]} ({len(target_dates)}日)")

    async with FureaiNetScraper(
        headless=True,
        user_id=os.getenv("FUREAI_USER_ID", ""),
        password=os.getenv("FUREAI_PASSWORD", ""),
    ) as s:
        slots = await s.check_availability(facility, target_dates)

    print(f"\n取得スロット数: {len(slots)}")
    # ステータス別集計
    from collections import Counter
    c = Counter(sl["status"] for sl in slots)
    for status, count in sorted(c.items()):
        print(f"  {status}: {count}")

    # 空き・一部空きを表示
    available = [sl for sl in slots if sl["status"] in ("available", "partial")]
    if available:
        print(f"\n空きあり ({len(available)}件):")
        for sl in available:
            print(f"  {sl['date']} {sl['area_name']} [{sl['status_raw']}]")
    else:
        print("\n空きなし（または受付期間外）")


asyncio.run(test())
