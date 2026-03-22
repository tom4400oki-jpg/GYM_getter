"""
定期スクレイプスケジューラー
APScheduler を使って一定間隔で空き確認を実行する
"""
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime

scheduler = AsyncIOScheduler(timezone="Asia/Tokyo")


def start_scheduler() -> AsyncIOScheduler:
    interval_min = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "30"))

    scheduler.add_job(
        scheduled_availability_check,
        trigger=IntervalTrigger(minutes=interval_min),
        id="availability_check",
        name="空き状況定期チェック",
        replace_existing=True,
    )
    scheduler.start()
    print(f"[Scheduler] 空き確認スケジューラー開始 (間隔: {interval_min}分)")
    return scheduler


async def scheduled_availability_check():
    """定期実行される空き確認タスク"""
    from backend.tasks import run_availability_check_all
    print(f"[Scheduler] 定期チェック開始 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    await run_availability_check_all()
