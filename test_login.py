"""ログイン動作確認スクリプト"""
import asyncio
import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()


async def test_login():
    from backend.scrapers.fureai_net import FureaiNetScraper

    user_id = os.getenv("FUREAI_USER_ID", "")
    password = os.getenv("FUREAI_PASSWORD", "")
    security_no = os.getenv("FUREAI_SECURITY_NUMBER", "")

    print(f"利用者番号: {user_id}")
    print(f"パスワード: {'*' * len(password)}")
    print(f"セキュリティ番号: {'(未設定)' if not security_no else '*' * len(security_no)}")
    print()

    async with FureaiNetScraper(
        headless=False,  # ブラウザを表示して動作確認
        user_id=user_id,
        password=password,
        security_no=security_no,
    ) as scraper:
        print("ログイン試行中...")
        result = await scraper.login()
        print(f"ログイン結果: {'成功' if result else '失敗'}")
        print(f"現在のURL: {scraper.page.url}")

        if scraper._flow_log:
            print("\nログ:")
            for entry in scraper._flow_log:
                print(f"  [{entry['time']}] {entry['msg']}")

        # スクリーンショット保存
        shot = await scraper.screenshot("login_test")
        print(f"\nスクリーンショット: {shot}")

        if result:
            # ページ内容を少し確認
            title = await scraper.page.title()
            print(f"ページタイトル: {title}")

        input("\n[Enter] で終了...")


asyncio.run(test_login())
