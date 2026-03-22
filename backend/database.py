from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.models import Base
import json
from pathlib import Path

DATABASE_URL = "sqlite:///./gym_getter.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """テーブル作成 + facilities.json からの初期データ投入"""
    Base.metadata.create_all(bind=engine)
    _seed_facilities()


def _seed_facilities():
    from backend.models import Facility
    facilities_path = Path(__file__).parent.parent / "facilities.json"
    if not facilities_path.exists():
        return

    with open(facilities_path, encoding="utf-8") as f:
        data = json.load(f)

    db = SessionLocal()
    try:
        for f_data in data["facilities"]:
            existing = db.get(Facility, f_data["id"])
            if existing:
                continue
            facility = Facility(
                id=f_data["id"],
                name=f_data["name"],
                facility_type=f_data["type"],
                address=f_data.get("address"),
                phone=f_data.get("phone"),
                hours=f_data.get("hours"),
                sports=f_data.get("sports", []),
                areas=f_data.get("areas", []),
                notes=f_data.get("notes"),
                is_active=f_data.get("active", True),
            )
            db.add(facility)
        db.commit()
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
