"""
GYM Getter - FastAPI バックエンド
"""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.database import init_db, get_db
from backend.models import Facility, BookingFlowStep, AvailabilitySlot, FacilityArea, ScrapeLog
from backend.scheduler import start_scheduler, scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時
    init_db()
    start_scheduler()
    yield
    # 終了時
    if scheduler.running:
        scheduler.shutdown()


app = FastAPI(
    title="GYM Getter",
    description="川崎市高津区 体育館・テニスコート 空き状況モニター",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────
# 施設 API
# ──────────────────────────────────────────

@app.get("/api/facilities")
def get_facilities(db: Session = Depends(get_db)):
    """施設一覧を返す"""
    facilities = db.query(Facility).all()
    return [
        {
            "id": f.id,
            "name": f.name,
            "type": f.facility_type,
            "address": f.address,
            "phone": f.phone,
            "hours": f.hours,
            "sports": f.sports,
            "areas": f.areas,
            "notes": f.notes,
            "is_active": f.is_active,
        }
        for f in facilities
    ]


# ──────────────────────────────────────────
# 予約フロー API
# ──────────────────────────────────────────

@app.get("/api/booking-flows/{facility_id}")
def get_booking_flow(facility_id: str, db: Session = Depends(get_db)):
    """指定施設の検出済み予約フローステップを返す"""
    steps = (
        db.query(BookingFlowStep)
        .filter(BookingFlowStep.facility_id == facility_id)
        .order_by(BookingFlowStep.step_number)
        .all()
    )
    return [
        {
            "step_number": s.step_number,
            "step_name": s.step_name,
            "url": s.url,
            "page_title": s.page_title,
            "form_fields": s.form_fields,
            "navigation_links": s.navigation_links,
            "screenshot_path": s.screenshot_path,
            "requires_login": s.requires_login,
            "notes": s.notes,
            "detected_at": s.detected_at.isoformat() if s.detected_at else None,
        }
        for s in steps
    ]


@app.get("/api/known-facilities")
def get_known_facilities():
    """FACILITY_NAV に登録されている施設一覧を返す（DB未登録でも表示）"""
    from backend.scrapers.fureai_net import FACILITY_NAV
    return [
        {"id": fid, "name": nav["building"], "region": nav["region"], "has_lottery": nav.get("has_lottery", False)}
        for fid, nav in FACILITY_NAV.items()
    ]


@app.post("/api/booking-flows/{facility_id}/detect")
async def trigger_detect_flow(facility_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """予約フロー検出をバックグラウンドで実行"""
    facility = db.get(Facility, facility_id)
    if not facility:
        raise HTTPException(status_code=404, detail="施設が見つかりません")

    from backend.tasks import run_detect_flow
    background_tasks.add_task(run_detect_flow, facility_id)
    return {"message": f"{facility.name} の予約フロー検出を開始しました"}


@app.post("/api/booking-flows/detect-all")
async def trigger_detect_all(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """全施設の予約フロー検出をバックグラウンドで実行"""
    from backend.tasks import run_detect_flow
    facilities = db.query(Facility).filter(Facility.is_active == True).all()
    for f in facilities:
        background_tasks.add_task(run_detect_flow, f.id)
    return {"message": f"{len(facilities)}施設のフロー検出を開始しました"}


# ──────────────────────────────────────────
# 空き状況 API
# ──────────────────────────────────────────

@app.get("/api/availability")
def get_availability(
    facility_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    booking_type: Optional[str] = None,
    area_name: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """空き状況スロット一覧を返す"""
    query = db.query(AvailabilitySlot)

    if facility_id:
        query = query.filter(AvailabilitySlot.facility_id == facility_id)
    if date_from:
        query = query.filter(AvailabilitySlot.date >= date_from)
    if date_to:
        query = query.filter(AvailabilitySlot.date <= date_to)
    if status:
        query = query.filter(AvailabilitySlot.status == status)
    if booking_type:
        query = query.filter(AvailabilitySlot.booking_type == booking_type)
    if area_name:
        query = query.filter(AvailabilitySlot.area_name == area_name)

    slots = query.order_by(AvailabilitySlot.date, AvailabilitySlot.area_name, AvailabilitySlot.time_start).all()

    return [
        {
            "id": s.id,
            "facility_id": s.facility_id,
            "area_name": s.area_name,
            "date": s.date,
            "time_start": s.time_start,
            "time_end": s.time_end,
            "status": s.status,
            "status_raw": s.status_raw,
            "booking_href": s.booking_href,
            "booking_type": s.booking_type,
            "checked_at": s.checked_at.isoformat() if s.checked_at else None,
        }
        for s in slots
    ]


@app.post("/api/availability/{facility_id}/check")
async def trigger_availability_check(
    facility_id: str,
    background_tasks: BackgroundTasks,
    days_ahead: int = 14,
    db: Session = Depends(get_db),
):
    """空き状況チェックをバックグラウンドで実行"""
    facility = db.get(Facility, facility_id)
    if not facility:
        raise HTTPException(status_code=404, detail="施設が見つかりません")

    from backend.tasks import run_availability_check
    background_tasks.add_task(run_availability_check, facility_id, days_ahead)
    return {"message": f"{facility.name} の空き確認を開始しました ({days_ahead}日分)"}


@app.post("/api/availability/check-all")
async def trigger_check_all(background_tasks: BackgroundTasks):
    """全エリアの空き確認をバックグラウンドで実行（発見済みエリアを1つずつ）"""
    from backend.tasks import run_check_all_areas
    background_tasks.add_task(run_check_all_areas)
    return {"message": "全エリアの空き確認を開始しました（エリアを1つずつ順番にチェックします）"}


# ──────────────────────────────────────────
# 施設エリア発見 API
# ──────────────────────────────────────────

@app.get("/api/facility-areas")
def get_facility_areas(db: Session = Depends(get_db)):
    """発見済みエリア一覧を返す（空き件数付き）"""
    from sqlalchemy import func
    areas = db.query(FacilityArea).order_by(
        FacilityArea.facility_id,
        FacilityArea.booking_type,
        FacilityArea.area_index,
    ).all()
    facilities_list = db.query(Facility).all()
    fac_map = {f.id: f.name for f in facilities_list}

    # エリアごとの空きカウントを一括取得
    slot_counts = db.query(
        AvailabilitySlot.facility_id,
        AvailabilitySlot.area_name,
        AvailabilitySlot.booking_type,
        AvailabilitySlot.status,
        func.count(AvailabilitySlot.id),
    ).group_by(
        AvailabilitySlot.facility_id,
        AvailabilitySlot.area_name,
        AvailabilitySlot.booking_type,
        AvailabilitySlot.status,
    ).all()

    # (facility_id, area_name, booking_type) -> {status: count}
    slot_map: dict = {}
    for fid, aname, btype, status, cnt in slot_counts:
        key = (fid, aname, btype)
        slot_map.setdefault(key, {})[status] = cnt

    result = []
    for a in areas:
        key = (a.facility_id, a.area_name, a.booking_type)
        counts = slot_map.get(key, {})
        available = counts.get("available", 0)
        partial = counts.get("partial", 0)
        open_cnt = counts.get("open", 0)
        occupied = counts.get("occupied", 0)
        total = sum(counts.values())
        result.append({
            "id": a.id,
            "facility_id": a.facility_id,
            "facility_name": fac_map.get(a.facility_id, a.facility_id),
            "area_name": a.area_name,
            "area_index": a.area_index,
            "booking_type": a.booking_type,
            "discovered_at": a.discovered_at.isoformat() if a.discovered_at else None,
            "last_checked": a.last_checked.isoformat() if a.last_checked else None,
            "check_status": a.check_status,
            # 空き状況サマリー
            "slots_total": total,
            "slots_available": available,
            "slots_partial": partial,
            "slots_open": open_cnt,
            "slots_occupied": occupied,
        })
    return result


@app.post("/api/facility-areas/discover")
async def trigger_discover_areas(background_tasks: BackgroundTasks):
    """全施設のエリア発見をバックグラウンドで実行"""
    from backend.tasks import run_discover_all_areas
    background_tasks.add_task(run_discover_all_areas)
    return {"message": "全施設のエリア発見を開始しました"}


@app.post("/api/facility-areas/discover/{facility_id}")
async def trigger_discover_facility_areas(facility_id: str, background_tasks: BackgroundTasks):
    """特定施設のエリア発見をバックグラウンドで実行"""
    from backend.tasks import run_discover_facility_areas
    background_tasks.add_task(run_discover_facility_areas, facility_id)
    return {"message": f"{facility_id} のエリア発見を開始しました"}


@app.post("/api/facility-areas/check-facility/{facility_id}")
async def trigger_check_facility_areas(
    facility_id: str,
    background_tasks: BackgroundTasks,
    days_ahead: int = 14,
    db: Session = Depends(get_db),
):
    """特定施設の全エリアを順番にチェック"""
    from backend.tasks import run_check_single_area
    areas = db.query(FacilityArea).filter(
        FacilityArea.facility_id == facility_id
    ).order_by(FacilityArea.booking_type, FacilityArea.area_index).all()
    if not areas:
        raise HTTPException(status_code=404, detail="発見済みエリアがありません。先にエリア発見を実行してください。")

    def _run_all():
        for a in areas:
            run_check_single_area(a.id, days_ahead)

    background_tasks.add_task(_run_all)
    return {"message": f"{facility_id} の{len(areas)}エリアのチェックを開始しました"}


@app.post("/api/facility-areas/{area_id}/check")
async def trigger_check_single_area(
    area_id: int,
    background_tasks: BackgroundTasks,
    days_ahead: int = 14,
    db: Session = Depends(get_db),
):
    """指定エリア1つの空き確認をバックグラウンドで実行"""
    area = db.get(FacilityArea, area_id)
    if not area:
        raise HTTPException(status_code=404, detail="エリアが見つかりません")
    from backend.tasks import run_check_single_area
    background_tasks.add_task(run_check_single_area, area_id, days_ahead)
    return {"message": f"{area.area_name} ({area.booking_type}) の空き確認を開始しました"}


# ──────────────────────────────────────────
# ログ API
# ──────────────────────────────────────────

@app.get("/api/logs")
def get_logs(limit: int = 100, db: Session = Depends(get_db)):
    """最近のスクレイプログを返す"""
    logs = (
        db.query(ScrapeLog)
        .order_by(ScrapeLog.started_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": l.id,
            "facility_id": l.facility_id,
            "task_type": l.task_type,
            "started_at": (l.started_at.isoformat() + "Z") if l.started_at else None,
            "completed_at": (l.completed_at.isoformat() + "Z") if l.completed_at else None,
            "status": l.status,
            "error_message": l.error_message,
            "result_count": l.result_count,
        }
        for l in logs
    ]


@app.get("/api/logs/{log_id}")
def get_log_detail(log_id: int, db: Session = Depends(get_db)):
    """ログの詳細（scraper _flow_log）を返す"""
    log = db.get(ScrapeLog, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="ログが見つかりません")
    return {
        "id": log.id,
        "facility_id": log.facility_id,
        "task_type": log.task_type,
        "started_at": (log.started_at.isoformat() + "Z") if log.started_at else None,
        "completed_at": (log.completed_at.isoformat() + "Z") if log.completed_at else None,
        "status": log.status,
        "error_message": log.error_message,
        "result_count": log.result_count,
        "detail_log": log.detail_log or [],
    }


# ──────────────────────────────────────────
# スクリーンショット提供
# ──────────────────────────────────────────

@app.get("/screenshots/{filename}")
def get_screenshot(filename: str):
    path = Path("screenshots") / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    return FileResponse(str(path))


# ──────────────────────────────────────────
# フロントエンド
# ──────────────────────────────────────────

frontend_dir = Path("frontend")
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
