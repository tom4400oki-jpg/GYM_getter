"""
定期スクレイプスケジューラー
APScheduler BackgroundScheduler を使って一定間隔で空き確認を実行する
"""
import os
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime

scheduler = BackgroundScheduler(timezone="Asia/Tokyo")


def start_scheduler() -> BackgroundScheduler:
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


def scheduled_availability_check():
    """定期実行される空き確認タスク"""
    from backend.tasks import run_availability_check_all
    print(f"[Scheduler] 定期チェック開始 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    run_availability_check_all()
