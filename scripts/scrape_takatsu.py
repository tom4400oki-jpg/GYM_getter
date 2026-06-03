"""
高津スポーツセンター 空き確認（予約のみ）スタンドアロン スクレイパー

既存の FureaiNetScraper を再利用し、3エリア（大体育室半面Ａ/Ｂ・小体育室）の
予約空き状況を取得して web/public/data.json に書き出す。
DB・FastAPI には依存しない。

使い方:
    python scripts/scrape_takatsu.py

環境変数（.env または GitHub Secrets）:
    FUREAI_USER_ID, FUREAI_PASSWORD, FUREAI_SECURITY_NUMBER
    SCRAPE_DAYS_AHEAD（任意, 既定 30）
    HEADLESS（任意, 既定 true）
"""
import os
import sys
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path

# Windows の cp932 コンソールでも日本語 print でクラッシュしないよう UTF-8 に固定
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# プロジェクトルートを import パスに追加（scripts/ から実行されるため）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from backend.scrapers.fureai_net import (  # noqa: E402
    FureaiNetScraper,
    FACILITY_NAV,
    get_target_dates,
)

FACILITY_ID = "takatsu_sports_center"
FACILITY_NAME = "高津スポーツセンター"
BOOKING_TYPE = "reservation"
OUTPUT_PATH = ROOT / "web" / "public" / "data.json"

WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]
PERIOD_ORDER = ["午前", "午後1", "午後2", "夜間"]
RESERVABLE = ("available", "partial")  # 予約可能とみなす


def _run_async(coro):
    """Windows では ProactorEventLoop を使う（Playwright のサブプロセス対応）"""
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return asyncio.run(coro)


def _scraper_kwargs() -> dict:
    return {
        "headless": os.getenv("HEADLESS", "true").lower() == "true",
        "user_id": os.getenv("FUREAI_USER_ID", ""),
        "password": os.getenv("FUREAI_PASSWORD", ""),
        "security_no": os.getenv("FUREAI_SECURITY_NUMBER", ""),
    }


async def _collect_records() -> list[dict]:
    """3エリアを順にチェックして日別レコード（時間帯付き）を返す"""
    nav = FACILITY_NAV[FACILITY_ID]
    areas = nav["areas"]  # [{"name": ..., "index": ...}, ...]
    days_ahead = int(os.getenv("SCRAPE_DAYS_AHEAD", "30"))
    target_dates = get_target_dates(days_ahead)

    all_records: list[dict] = []
    async with FureaiNetScraper(**_scraper_kwargs()) as scraper:
        for area in areas:
            def on_progress(msg, _a=area["name"]):
                print(f"[{_a}] {msg}", flush=True)

            recs = await scraper.check_area_days(
                FACILITY_ID,
                area["name"],
                area["index"],
                BOOKING_TYPE,
                target_dates,
                on_progress=on_progress,
            )
            print(f"[{area['name']}] {len(recs)} 日取得", flush=True)
            all_records.extend(recs)
    return all_records


def _build_payload(records: list[dict]) -> dict:
    """日別レコードを data.json の構造へ整形"""
    nav = FACILITY_NAV[FACILITY_ID]
    area_names = [a["name"] for a in nav["areas"]]

    # date -> { area_name -> record }
    by_date: dict[str, dict] = {}
    for r in records:
        date = r.get("date")
        area = r.get("area_name")
        if not date or area not in area_names:
            continue
        by_date.setdefault(date, {})[area] = r

    days = []
    for date in sorted(by_date):
        d = datetime.strptime(date, "%Y-%m-%d")
        by_area = {}
        for area in area_names:
            r = by_date[date].get(area)
            if not r:
                by_area[area] = {"status": "unknown", "statusRaw": "", "periods": {}}
            else:
                by_area[area] = {
                    "status": r.get("status", "unknown"),
                    "statusRaw": r.get("status_raw", ""),
                    "periods": r.get("periods", {}),
                }
        days.append({
            "date": date,
            "weekday": WEEKDAYS_JA[d.weekday()],
            "byArea": by_area,
        })

    return {
        "facility": FACILITY_NAME,
        "bookingType": BOOKING_TYPE,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "areas": area_names,
        "periodOrder": PERIOD_ORDER,
        "days": days,
    }


def main() -> int:
    print(f"=== 高津スポーツセンター 空き確認 開始 {datetime.now():%Y-%m-%d %H:%M:%S} ===", flush=True)
    records = _run_async(_collect_records())
    payload = _build_payload(records)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    n_days = len(payload["days"])
    n_reservable = sum(
        1
        for d in payload["days"]
        if any(a["status"] in RESERVABLE for a in d["byArea"].values())
    )
    print(f"=== 完了: {n_days}日 × {len(payload['areas'])}エリア / 予約可能{n_reservable}日 ===", flush=True)
    print(f"出力: {OUTPUT_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
