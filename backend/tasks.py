"""
スクレイプタスク（API エンドポイントとスケジューラーから呼ばれる）

Note: Playwright は Python 3.13 + Windows + uvicorn の event loop 内で
      subprocess を起動できないため、各タスクは独立した event loop で実行する。
      FastAPI の BackgroundTasks に同期関数を渡すとスレッドプールで実行されるため、
      内部で asyncio.run() を使っても問題ない。
"""
import os
import sys
import asyncio
import traceback
from datetime import datetime, timezone

from backend.database import SessionLocal
from backend.models import BookingFlowStep, AvailabilitySlot, ScrapeLog, Facility
from backend.scrapers.fureai_net import FureaiNetScraper, get_target_dates


def _run_async(coro):
    """Windows の SelectorEventLoop では subprocess が動かないため ProactorEventLoop を明示指定"""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    else:
        return asyncio.run(coro)


def _get_scraper_kwargs() -> dict:
    return {
        "headless": os.getenv("HEADLESS", "true").lower() == "true",
        "user_id": os.getenv("FUREAI_USER_ID", ""),
        "password": os.getenv("FUREAI_PASSWORD", ""),
        "security_no": os.getenv("FUREAI_SECURITY_NUMBER", ""),
    }


# ──────────────────────────────────────────
# 非同期スクレイピング処理（独立 event loop で実行）
# ──────────────────────────────────────────

async def _async_detect_flow(facility_dict: dict) -> list[dict]:
    async with FureaiNetScraper(**_get_scraper_kwargs()) as scraper:
        return await scraper.detect_booking_flow(facility_dict)


async def _async_check_availability(facility_dict: dict, target_dates: list[str]) -> list[dict]:
    async with FureaiNetScraper(**_get_scraper_kwargs()) as scraper:
        return await scraper.check_availability(facility_dict, target_dates)


# ──────────────────────────────────────────
# 同期タスク（FastAPI BackgroundTasks / APScheduler から呼ぶ）
# ──────────────────────────────────────────

def run_detect_flow(facility_id: str) -> dict:
    """指定施設の予約フローを検出してDBに保存"""
    db = SessionLocal()
    log = ScrapeLog(facility_id=facility_id, task_type="detect_flow", status="running")
    db.add(log)
    db.commit()

    try:
        facility = db.get(Facility, facility_id)
        if not facility:
            raise ValueError(f"施設が見つかりません: {facility_id}")

        facility_dict = {
            "id": facility.id,
            "name": facility.name,
            "type": facility.facility_type,
        }

        # 独立した event loop で Playwright を実行
        steps = _run_async(_async_detect_flow(facility_dict))

        db.query(BookingFlowStep).filter(BookingFlowStep.facility_id == facility_id).delete()
        for step_data in steps:
            step = BookingFlowStep(
                facility_id=facility_id,
                step_number=step_data.get("step_number"),
                step_name=step_data.get("step_name"),
                url=step_data.get("url"),
                page_title=step_data.get("page_title"),
                form_fields=step_data.get("form_fields"),
                navigation_links=step_data.get("navigation_links"),
                screenshot_path=step_data.get("screenshot_path"),
                requires_login=step_data.get("requires_login", False),
                notes=step_data.get("notes"),
            )
            db.add(step)

        log.status = "success"
        log.result_count = len(steps)
        log.completed_at = datetime.now(timezone.utc)
        db.commit()
        print(f"[通知] [フロー検出完了] {facility.name}: {len(steps)}ステップ検出")
        return {"status": "success", "steps": len(steps)}

    except Exception as e:
        log.status = "error"
        log.error_message = f"{type(e).__name__}: {e}" if str(e) else traceback.format_exc()[-300:]
        log.completed_at = datetime.now(timezone.utc)
        db.commit()
        print(f"[Tasks] run_detect_flow エラー ({facility_id}): {traceback.format_exc()}")
    finally:
        db.close()


def run_availability_check(facility_id: str, days_ahead: int = 14) -> dict:
    """指定施設の空き状況をチェックしてDBに保存"""
    db = SessionLocal()
    log = ScrapeLog(facility_id=facility_id, task_type="check_availability", status="running")
    db.add(log)
    db.commit()

    try:
        facility = db.get(Facility, facility_id)
        if not facility:
            raise ValueError(f"施設が見つかりません: {facility_id}")

        facility_dict = {"id": facility.id, "name": facility.name}
        target_dates = get_target_dates(days_ahead)

        # 独立した event loop で Playwright を実行
        slots = _run_async(_async_check_availability(facility_dict, target_dates))

        db.query(AvailabilitySlot).filter(AvailabilitySlot.facility_id == facility_id).delete()
        new_available = 0
        for slot_data in slots:
            slot = AvailabilitySlot(**slot_data)
            db.add(slot)
            if slot_data.get("status") == "available":
                new_available += 1

        log.status = "success"
        log.result_count = len(slots)
        log.completed_at = datetime.now(timezone.utc)
        db.commit()

        if new_available > 0:
            print(f"[通知] [空き発見！] {facility.name}: {new_available}枠の空きが見つかりました")
        return {"status": "success", "slots": len(slots), "available": new_available}

    except Exception as e:
        log.status = "error"
        log.error_message = f"{type(e).__name__}: {e}" if str(e) else traceback.format_exc()[-300:]
        log.completed_at = datetime.now(timezone.utc)
        db.commit()
        print(f"[Tasks] run_availability_check エラー ({facility_id}): {traceback.format_exc()}")
    finally:
        db.close()


def run_availability_check_all():
    """全アクティブ施設の空き確認"""
    db = SessionLocal()
    try:
        facilities = db.query(Facility).filter(Facility.is_active == True).all()
        for facility in facilities:
            try:
                run_availability_check(facility.id)
            except Exception as e:
                print(f"[Tasks] {facility.name} エラー: {e}")
    finally:
        db.close()
