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
import urllib.request
import urllib.error
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

APP_URL = "https://gym-getter.web.app"
LIVE_DATA_URL = f"{APP_URL}/data.json"
SHORT_AREA = {"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ", "小体育室": "小"}


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


# ──────────────────────────────────────────
# 空き通知（前回=公開中の data.json と差分を取り、新着のみ通知）
# ──────────────────────────────────────────

def _reservable_keys(payload: dict) -> dict:
    """予約可能な枠の集合を {(date, area, label): True} で返す。
    label は時間帯（午前等）。時間帯不明な日は '終日'。"""
    keys: dict[tuple, str] = {}
    for day in payload.get("days", []):
        date = day["date"]
        wd = day.get("weekday", "")
        for area, a in day.get("byArea", {}).items():
            periods = a.get("periods") or {}
            if periods:
                for p, st in periods.items():
                    if st in RESERVABLE:
                        keys[(date, area, p)] = wd
            elif a.get("status") in RESERVABLE:
                keys[(date, area, "終日")] = wd
    return keys


def _fetch_previous() -> dict | None:
    """公開中の data.json（前回結果）を取得。失敗時は None。"""
    try:
        req = urllib.request.Request(LIVE_DATA_URL, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        print(f"[通知] 前回データ取得不可（初回扱い）: {e}", flush=True)
        return None


def _format_keys(keys: dict) -> list:
    """{(date, area, label): weekday} を ・MM/DD(曜) エリア … 時間帯 の行リストに整形。"""
    grouped: dict[tuple, list] = {}
    wd_map: dict[str, str] = {}
    for (date, area, label), wd in sorted(keys.items()):
        grouped.setdefault((date, area), []).append(label)
        wd_map[date] = wd
    out = []
    for (date, area) in sorted(grouped.keys()):
        md = date[5:].replace("-", "/")
        labels = grouped[(date, area)]
        labels = [l for l in PERIOD_ORDER if l in labels] + [l for l in labels if l == "終日"]
        short = SHORT_AREA.get(area, area)
        out.append(f"・{md}({wd_map.get(date,'')}) {short} … {'・'.join(labels)}")
    return out


def _build_notify_message(added: dict) -> str:
    """新しく予約できるようになった枠だけを通知メッセージに整形する。"""
    lines = ["🏀 高津スポーツセンター 新着の空き"]
    lines += _format_keys(added)
    lines.append("")
    lines.append(f"▶ {APP_URL}")
    return "\n".join(lines)[:1900]


LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"


def _notify_line(added: dict) -> None:
    """新しく予約できるようになった枠を LINE（Messaging API ブロードキャスト）で通知する。
    LINE公式アカウントを友だち追加したメンバー全員に届く。"""
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if not token:
        print("[通知] LINE_CHANNEL_ACCESS_TOKEN 未設定のため通知スキップ", flush=True)
        return

    content = _build_notify_message(added)
    grouped_count = len({(d, a) for (d, a, _l) in added})
    try:
        data = json.dumps({"messages": [{"type": "text", "text": content}]}).encode("utf-8")
        req = urllib.request.Request(
            LINE_BROADCAST_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        urllib.request.urlopen(req, timeout=15)
        print(f"[通知] LINE送信OK（{grouped_count}件）", flush=True)
    except Exception as e:
        body = ""
        if isinstance(e, urllib.error.HTTPError):
            try:
                body = e.read().decode("utf-8")[:200]
            except Exception:
                pass
        print(f"[通知] LINE送信失敗: {e} {body}", flush=True)


def _set_output(key: str, value: str) -> None:
    """GitHub Actions のステップ出力に書き込む（ローカルでは無視）。"""
    gh = os.getenv("GITHUB_OUTPUT")
    if gh:
        try:
            with open(gh, "a", encoding="utf-8") as f:
                f.write(f"{key}={value}\n")
        except Exception:
            pass


def main() -> int:
    print(f"=== 高津スポーツセンター 空き確認 開始 {datetime.now():%Y-%m-%d %H:%M:%S} ===", flush=True)

    # 通知用に前回（公開中）データを先に取得
    previous = _fetch_previous()

    records = _run_async(_collect_records())
    payload = _build_payload(records)

    n_days = len(payload["days"])

    # メンテ時間/ログイン停止/ナビ失敗で空 → 上書きせず終了（ライブの既存データを保持）
    if n_days == 0:
        print("[警告] 取得0件（ふれあいネットのメンテ時間/ログイン停止の可能性）", flush=True)
        print("       data.json は更新せず、ビルド/デプロイ/通知をスキップします", flush=True)
        _set_output("deploy", "false")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _set_output("deploy", "true")

    n_reservable = sum(
        1
        for d in payload["days"]
        if any(a["status"] in RESERVABLE for a in d["byArea"].values())
    )
    print(f"=== 完了: {n_days}日 × {len(payload['areas'])}エリア / 予約可能{n_reservable}日 ===", flush=True)
    print(f"出力: {OUTPUT_PATH}", flush=True)

    # 差分通知（取得が空（ログイン失敗等）の場合は誤通知を避けるためスキップ）
    if payload["days"]:
        new_now = _reservable_keys(payload)
        if previous is None:
            print("[通知] 前回データなしのため初回は通知しません", flush=True)
        else:
            old = _reservable_keys(previous)
            added = {k: v for k, v in new_now.items() if k not in old}
            if added:
                print(f"[通知] 新着の予約可能枠 {len(added)}件", flush=True)
                _notify_line(added)
            else:
                print("[通知] 新着なし", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
