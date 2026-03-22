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
from backend.models import Facility, BookingFlowStep, AvailabilitySlot, ScrapeLog
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

    slots = query.order_by(AvailabilitySlot.date, AvailabilitySlot.time_start).all()

    return [
        {
            "id": s.id,
            "facility_id": s.facility_id,
            "area_name": s.area_name,
            "date": s.date,
            "time_start": s.time_start,
            "time_end": s.time_end,
            "status": s.status,
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
    """全施設の空き確認をバックグラウンドで実行"""
    from backend.tasks import run_availability_check_all
    background_tasks.add_task(run_availability_check_all)
    return {"message": "全施設の空き確認を開始しました"}


# ──────────────────────────────────────────
# ログ API
# ──────────────────────────────────────────

@app.get("/api/logs")
def get_logs(limit: int = 50, db: Session = Depends(get_db)):
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
