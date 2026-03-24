"""
スクレイプタスク（API エンドポイントとスケジューラーから呼ばれる）
"""
import os
import sys
import asyncio
import traceback
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from backend.database import SessionLocal
from backend.models import BookingFlowStep, AvailabilitySlot, FacilityArea, ScrapeLog, Facility
from backend.scrapers.fureai_net import FureaiNetScraper, get_target_dates

SCRAPE_TIMEOUT = int(os.getenv("SCRAPE_TIMEOUT", "900"))  # 15分


def _run_async(coro):
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


def _save_progress(log_id: int, msg: str):
    """中間進捗をDBに保存する"""
    db = SessionLocal()
    try:
        log = db.get(ScrapeLog, log_id)
        if log:
            log.error_message = f"[進捗] {msg}"
            db.commit()
    except Exception:
        pass
    finally:
        db.close()


# ──────────────────────────────────────────
# 非同期ラッパー
# ──────────────────────────────────────────

async def _async_discover_facility_areas(facility_id: str, on_progress=None):
    async with FureaiNetScraper(**_get_scraper_kwargs()) as scraper:
        areas = await scraper.discover_facility_areas(facility_id, on_progress=on_progress)
        return areas, scraper._flow_log


async def _async_discover_all_areas(on_progress=None):
    async with FureaiNetScraper(**_get_scraper_kwargs()) as scraper:
        areas = await scraper.discover_all_areas(on_progress=on_progress)
        return areas, scraper._flow_log


async def _async_check_single_area(facility_id, area_name, area_index, booking_type, target_dates, on_progress=None):
    async with FureaiNetScraper(**_get_scraper_kwargs()) as scraper:
        slots = await scraper.check_single_area(
            facility_id, area_name, area_index, booking_type, target_dates,
            on_progress=on_progress,
        )
        return slots, scraper._flow_log


async def _async_detect_flow(facility_dict: dict):
    async with FureaiNetScraper(**_get_scraper_kwargs()) as scraper:
        steps = await scraper.detect_booking_flow(facility_dict)
        return steps, scraper._flow_log


# ──────────────────────────────────────────
# 施設エリア発見
# ──────────────────────────────────────────

def run_discover_facility_areas(facility_id: str) -> dict:
    """特定施設のエリアを発見してDBに保存"""
    db = SessionLocal()
    log = ScrapeLog(facility_id=facility_id, task_type="discover_areas", status="running")
    db.add(log)
    db.commit()
    log_id = log.id

    def on_progress(msg):
        _save_progress(log_id, msg)
        print(f"[Discovery:{facility_id}] {msg}")

    try:
        areas, flow_log = _run_async(
            asyncio.wait_for(_async_discover_facility_areas(facility_id, on_progress), timeout=SCRAPE_TIMEOUT)
        )

        # この施設のエリアだけ削除して再登録
        db.query(FacilityArea).filter(FacilityArea.facility_id == facility_id).delete()
        for a in areas:
            db.add(FacilityArea(
                facility_id=a["facility_id"],
                area_name=a["area_name"],
                area_index=a["area_index"],
                booking_type=a["booking_type"],
                discovered_at=datetime.now(timezone.utc),
            ))

        log.status = "success"
        log.result_count = len(areas)
        log.completed_at = datetime.now(timezone.utc)
        log.detail_log = flow_log
        log.error_message = None
        db.commit()
        print(f"[Discovery:{facility_id}] 完了: {len(areas)}エリア発見")
        return {"status": "success", "areas": len(areas)}

    except asyncio.TimeoutError:
        log.status = "error"
        log.error_message = f"タイムアウト ({SCRAPE_TIMEOUT}秒)"
        log.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        log.status = "error"
        log.error_message = traceback.format_exc()
        log.completed_at = datetime.now(timezone.utc)
        db.commit()
        print(f"[Discovery:{facility_id}] エラー: {traceback.format_exc()}")
    finally:
        db.close()


def run_discover_all_areas() -> dict:
    """全施設の予約・抽選エリアを発見してDBに保存"""
    db = SessionLocal()
    log = ScrapeLog(facility_id="all", task_type="discover_areas", status="running")
    db.add(log)
    db.commit()
    log_id = log.id

    def on_progress(msg):
        _save_progress(log_id, msg)
        print(f"[Discovery] {msg}")

    try:
        areas, flow_log = _run_async(
            asyncio.wait_for(_async_discover_all_areas(on_progress), timeout=SCRAPE_TIMEOUT)
        )

        # 全エリアをリフレッシュ（再発見のたびに上書き）
        db.query(FacilityArea).delete()
        for a in areas:
            db.add(FacilityArea(
                facility_id=a["facility_id"],
                area_name=a["area_name"],
                area_index=a["area_index"],
                booking_type=a["booking_type"],
                discovered_at=datetime.now(timezone.utc),
            ))

        log.status = "success"
        log.result_count = len(areas)
        log.completed_at = datetime.now(timezone.utc)
        log.detail_log = flow_log
        log.error_message = None
        db.commit()
        print(f"[Discovery] 完了: {len(areas)}エリア発見")
        return {"status": "success", "areas": len(areas)}

    except asyncio.TimeoutError:
        log.status = "error"
        log.error_message = f"タイムアウト ({SCRAPE_TIMEOUT}秒)"
        log.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        log.status = "error"
        log.error_message = traceback.format_exc()
        log.completed_at = datetime.now(timezone.utc)
        db.commit()
        print(f"[Discovery] エラー: {traceback.format_exc()}")
    finally:
        db.close()


# ──────────────────────────────────────────
# 個別エリアチェック
# ──────────────────────────────────────────

def run_check_single_area(area_id: int, days_ahead: int = 14) -> dict:
    """指定エリア1つの空き確認"""
    db = SessionLocal()
    area_rec = db.get(FacilityArea, area_id)
    if not area_rec:
        db.close()
        return {"status": "error", "message": "エリアが見つかりません"}

    # エリアのステータスを running に
    area_rec.check_status = "running"
    db.commit()

    log = ScrapeLog(
        facility_id=area_rec.facility_id,
        task_type="check_single_area",
        status="running",
        error_message=f"[進捗] {area_rec.area_name} ({area_rec.booking_type})",
    )
    db.add(log)
    db.commit()
    log_id = log.id

    def on_progress(msg):
        _save_progress(log_id, msg)
        print(f"[SingleArea] {msg}")

    try:
        target_dates = get_target_dates(days_ahead)

        slots, flow_log = _run_async(
            asyncio.wait_for(
                _async_check_single_area(
                    area_rec.facility_id,
                    area_rec.area_name,
                    area_rec.area_index,
                    area_rec.booking_type,
                    target_dates,
                    on_progress=on_progress,
                ),
                timeout=300,  # 1エリア5分
            )
        )

        # 当該エリアの既存スロットを削除して新規保存
        db.query(AvailabilitySlot).filter(
            AvailabilitySlot.facility_id == area_rec.facility_id,
            AvailabilitySlot.area_name == area_rec.area_name,
            AvailabilitySlot.booking_type == area_rec.booking_type,
        ).delete()

        _slot_fields = {c.name for c in AvailabilitySlot.__table__.columns}
        new_available = 0
        for slot_data in slots:
            filtered = {k: v for k, v in slot_data.items() if k in _slot_fields}
            slot = AvailabilitySlot(**filtered)
            db.add(slot)
            if slot_data.get("status") in ("available", "partial"):
                new_available += 1

        area_rec.check_status = "success"
        area_rec.last_checked = datetime.now(timezone.utc)

        log.status = "success"
        log.result_count = len(slots)
        log.completed_at = datetime.now(timezone.utc)
        log.detail_log = flow_log
        log.error_message = None
        db.commit()

        print(f"[SingleArea] {area_rec.area_name}/{area_rec.booking_type}: {len(slots)}スロット (空き{new_available})")
        return {"status": "success", "slots": len(slots), "available": new_available}

    except asyncio.TimeoutError:
        area_rec.check_status = "error"
        log.status = "error"
        log.error_message = "タイムアウト (300秒)"
        log.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        area_rec.check_status = "error"
        log.status = "error"
        log.error_message = traceback.format_exc()
        log.completed_at = datetime.now(timezone.utc)
        db.commit()
        print(f"[SingleArea] エラー: {traceback.format_exc()}")
    finally:
        db.close()


def run_check_all_areas(days_ahead: int = 14):
    """発見済み全エリアを1つずつ順番にチェック"""
    db = SessionLocal()
    try:
        areas = db.query(FacilityArea).order_by(
            FacilityArea.facility_id,
            FacilityArea.booking_type,
            FacilityArea.area_index,
        ).all()
        area_ids = [a.id for a in areas]
    finally:
        db.close()

    print(f"[CheckAll] {len(area_ids)}エリアを順番にチェックします")
    for area_id in area_ids:
        try:
            run_check_single_area(area_id, days_ahead)
        except Exception as e:
            print(f"[CheckAll] area_id={area_id} エラー: {e}")


# ──────────────────────────────────────────
# 旧タスク（互換性のため残す）
# ──────────────────────────────────────────

def run_detect_flow(facility_id: str) -> dict:
    db = SessionLocal()
    log = ScrapeLog(facility_id=facility_id, task_type="detect_flow", status="running")
    db.add(log)
    db.commit()

    try:
        facility = db.get(Facility, facility_id)
        if not facility:
            raise ValueError(f"施設が見つかりません: {facility_id}")
        facility_dict = {"id": facility.id, "name": facility.name, "type": facility.facility_type}

        steps, flow_log = _run_async(
            asyncio.wait_for(_async_detect_flow(facility_dict), timeout=SCRAPE_TIMEOUT)
        )

        db.query(BookingFlowStep).filter(BookingFlowStep.facility_id == facility_id).delete()
        for step_data in steps:
            db.add(BookingFlowStep(
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
            ))

        log.status = "success"
        log.result_count = len(steps)
        log.completed_at = datetime.now(timezone.utc)
        log.detail_log = flow_log
        log.error_message = None
        db.commit()
        return {"status": "success", "steps": len(steps)}

    except Exception:
        log.status = "error"
        log.error_message = traceback.format_exc()
        log.completed_at = datetime.now(timezone.utc)
        db.commit()
        print(f"[Tasks] detect_flow エラー ({facility_id}): {traceback.format_exc()}")
    finally:
        db.close()


def run_availability_check(facility_id: str, days_ahead: int = 14) -> dict:
    """施設単位での空き確認（旧インターフェース。発見済みエリアがあればそちらを優先）"""
    # 発見済みエリアがあればエリア単位チェックに委譲
    db = SessionLocal()
    try:
        areas = db.query(FacilityArea).filter(FacilityArea.facility_id == facility_id).all()
        area_ids = [a.id for a in areas]
    finally:
        db.close()

    if area_ids:
        for aid in area_ids:
            try:
                run_check_single_area(aid, days_ahead)
            except Exception as e:
                print(f"[Tasks] area_id={aid} エラー: {e}")
        return {"status": "success", "message": f"{len(area_ids)}エリアチェック完了"}

    # 発見済みエリアがなければ発見 → チェックの順で実行
    print(f"[Tasks] {facility_id}: 発見済みエリアなし。先にエリア発見を実行してください。")
    return {"status": "skipped", "message": "エリア未発見。まず「エリア発見」を実行してください。"}


def run_availability_check_all():
    """全施設チェック（旧）→ 発見済みエリアを全部チェック"""
    run_check_all_areas()
