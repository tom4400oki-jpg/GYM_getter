from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, JSON
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime


class Base(DeclarativeBase):
    pass


class Facility(Base):
    __tablename__ = "facilities"

    id = Column(String, primary_key=True)          # facilities.json の id と一致
    name = Column(String, nullable=False)
    facility_type = Column(String)                  # gym / tennis_court
    address = Column(String)
    phone = Column(String)
    hours = Column(String)
    sports = Column(JSON)                           # ["basketball", "volleyball", ...]
    areas = Column(JSON)                            # [{"name": ..., "capacity_note": ...}]
    system_name = Column(String, default="fureai_net")
    notes = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class BookingFlowStep(Base):
    """予約フローの各ステップを記録"""
    __tablename__ = "booking_flow_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    facility_id = Column(String, nullable=False)
    step_number = Column(Integer)
    step_name = Column(String)
    url = Column(String)
    page_title = Column(String)
    form_fields = Column(JSON)           # [{tag, name, type, options, required}]
    navigation_links = Column(JSON)      # [{text, href}]
    screenshot_path = Column(String)
    requires_login = Column(Boolean, default=False)
    notes = Column(Text)
    detected_at = Column(DateTime, default=datetime.utcnow)


class AvailabilitySlot(Base):
    """空き状況スロット"""
    __tablename__ = "availability_slots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    facility_id = Column(String, nullable=False)
    area_name = Column(String)           # 大体育室・全面 など
    date = Column(String)                # YYYY-MM-DD
    time_start = Column(String)          # HH:MM
    time_end = Column(String)            # HH:MM
    status = Column(String)              # available / partial / occupied / closed / open / unknown
    status_raw = Column(String)          # ふれあいネット元の表示文字（例: 一部空き）
    booking_href = Column(String)        # 予約クリック用URL（空きの場合のみ）
    booking_type = Column(String, default="reservation")  # reservation / lottery
    checked_at = Column(DateTime, default=datetime.utcnow)


class FacilityArea(Base):
    """ふれあいネットから発見した予約可能エリア一覧"""
    __tablename__ = "facility_areas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    facility_id = Column(String, nullable=False)
    area_name = Column(String, nullable=False)   # サイト上の実際のリンクテキスト
    area_index = Column(Integer)                 # 施設一覧ページ内の出現順（0始まり）
    booking_type = Column(String, nullable=False)  # reservation / lottery
    discovered_at = Column(DateTime, default=datetime.utcnow)
    last_checked = Column(DateTime)
    check_status = Column(String)                # null / running / success / error


class ScrapeLog(Base):
    """スクレイプ実行ログ"""
    __tablename__ = "scrape_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    facility_id = Column(String)
    task_type = Column(String)           # detect_flow / check_availability
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    status = Column(String)              # running / success / error
    error_message = Column(Text)
    result_count = Column(Integer, default=0)
    detail_log = Column(JSON)            # スクレイパーの詳細ログ [{time, msg}]
