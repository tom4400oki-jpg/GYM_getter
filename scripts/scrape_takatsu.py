"""
高津スポーツセンター 空き確認（予約のみ）スタンドアロン スクレイパー

既存の FureaiNetScraper を再利用し、3エリア（大体育室半面Ａ/Ｂ・小体育室）の
予約空き状況を取得して web/public/data.json に書き出す。
DB・FastAPI には依存しない。

安定性（サイレント故障対策）:
  - ログイン失敗を「メンテ時間（正常）」と「真の失敗（要対応）」で区別する
  - エリア単位で成否を判定し、一部失敗時はそのエリアだけ前回値を保持・通知抑止する
  - 異常（真のログイン失敗・全エリア失敗・部分失敗）は管理者に通知し Actions に注釈を出す

使い方:
    python scripts/scrape_takatsu.py

環境変数（.env または GitHub Secrets）:
    FUREAI_USER_ID, FUREAI_PASSWORD, FUREAI_SECURITY_NUMBER
    LINE_CHANNEL_ACCESS_TOKEN（任意, 利用者向けブロードキャスト通知）
    LINE_ADMIN_USER_ID（任意, 管理者向けエラー push 通知の宛先）
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

# ──────────────────────────────────────────
# スクレイプ対象の設定（単一の真実）
# 施設を増やすときは、まず backend/scrapers/fureai_net.py の FACILITY_NAV に
# 施設・エリアを定義し、ここの FACILITY_ID 等をその施設に合わせて差し替える。
# （複数施設の同時取得は将来の拡張。現状は1施設＝1 data.json 構成）
# ──────────────────────────────────────────
FACILITY_ID = "takatsu_sports_center"
FACILITY_NAME = "高津スポーツセンター"
BOOKING_TYPE = "reservation"
NOTIFY_EMOJI = "🏀"
OUTPUT_PATH = ROOT / "web" / "public" / "data.json"

WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]
PERIOD_ORDER = ["午前", "午後1", "午後2", "夜間"]
RESERVABLE = ("available", "partial")  # 予約可能とみなす

APP_URL = "https://gym-getter.web.app"
LIVE_DATA_URL = f"{APP_URL}/data.json"
# 予約申込みページ（施設トップではなく予約導線へ寄せる）
RESERVE_URL = "https://www.fureai-net.city.kawasaki.jp/web/"
SHORT_AREA = {"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ", "小体育室": "小"}

LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


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


# ──────────────────────────────────────────
# スクレイプ（エリア単位で成否を分離して収集）
# ──────────────────────────────────────────

async def _collect_records() -> tuple[dict, bool, bool]:
    """3エリアを順にチェック。
    戻り値: (by_area, login_ok, maintenance)
      by_area: { area_name: [日別レコード, ...] }（失敗エリアは [] か全unknown）
    """
    nav = FACILITY_NAV[FACILITY_ID]
    areas = nav["areas"]  # [{"name": ..., "index": ...}, ...]
    days_ahead = int(os.getenv("SCRAPE_DAYS_AHEAD", "30"))
    target_dates = get_target_dates(days_ahead)

    by_area: dict[str, list] = {}
    async with FureaiNetScraper(**_scraper_kwargs()) as scraper:
        # 先に明示ログインして、失敗の種別（メンテ/真の失敗）を判定できるようにする
        login_ok = await scraper.login()
        if not login_ok:
            return by_area, False, scraper.maintenance

        for area in areas:
            def on_progress(msg, _a=area["name"]):
                print(f"[{_a}] {msg}", flush=True)

            try:
                recs = await scraper.check_area_days(
                    FACILITY_ID,
                    area["name"],
                    area["index"],
                    BOOKING_TYPE,
                    target_dates,
                    on_progress=on_progress,
                )
            except Exception as e:
                print(f"[{area['name']}] 取得エラー: {e}", flush=True)
                recs = []
            print(f"[{area['name']}] {len(recs)} 日取得", flush=True)
            by_area[area["name"]] = recs
    return by_area, True, False


def _area_ok(recs: list) -> bool:
    """エリアの取得が成功とみなせるか。
    0日 or 全て unknown は失敗扱い（ナビ失敗・構造変化のサイレント故障を検知）。"""
    if not recs:
        return False
    known = sum(1 for r in recs if r.get("status") not in ("unknown", "", None))
    return known > 0


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
        "reserveUrl": RESERVE_URL,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "areas": area_names,
        "periodOrder": PERIOD_ORDER,
        "days": days,
    }


def _overlay_previous(payload: dict, previous: dict | None, areas: list[str]) -> None:
    """失敗したエリアについて、前回（公開中）の値を payload に上書きしてユーザー表示の退行を防ぐ。"""
    if not previous or not areas:
        return
    prev_by_date = {d["date"]: d for d in previous.get("days", [])}
    for day in payload["days"]:
        pd = prev_by_date.get(day["date"])
        if not pd:
            continue
        for area in areas:
            prev_area = pd.get("byArea", {}).get(area)
            if prev_area:
                day["byArea"][area] = prev_area


# ──────────────────────────────────────────
# 空き通知（前回=公開中の data.json と差分を取り、新着のみ通知）
# ──────────────────────────────────────────

def _reservable_keys(payload: dict, only_areas: list[str] | None = None) -> dict:
    """予約可能な枠の集合を {(date, area, label): weekday} で返す。
    label は時間帯（午前等）。時間帯不明な日は '終日'。
    only_areas を渡すと、そのエリアのみを母集合にする（失敗エリアを差分から除外）。"""
    keys: dict[tuple, str] = {}
    for day in payload.get("days", []):
        date = day["date"]
        wd = day.get("weekday", "")
        for area, a in day.get("byArea", {}).items():
            if only_areas is not None and area not in only_areas:
                continue
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
    lines = [f"{NOTIFY_EMOJI} {FACILITY_NAME} 新着の空き"]
    lines += _format_keys(added)
    lines.append("")
    lines.append(f"▶ {APP_URL}")
    return "\n".join(lines)[:1900]


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


def _notify_admin(reason: str) -> None:
    """管理者向けの異常アラート。GitHub Actions の注釈を必ず出し、
    LINE_ADMIN_USER_ID が設定されていれば管理者個人へ push する。"""
    msg = f"⚠ GYM Getter 異常: {reason}"
    # GitHub Actions のログ注釈（ジョブの Annotations に表示される）
    print(f"::warning title=GYM Getter::{msg}", flush=True)
    print(f"[管理者通知] {msg}", flush=True)

    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    admin = os.getenv("LINE_ADMIN_USER_ID", "").strip()
    if not (token and admin):
        return
    try:
        data = json.dumps({
            "to": admin,
            "messages": [{"type": "text", "text": msg[:1900]}],
        }).encode("utf-8")
        req = urllib.request.Request(
            LINE_PUSH_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        urllib.request.urlopen(req, timeout=15)
        print("[管理者通知] LINE push OK", flush=True)
    except Exception as e:
        print(f"[管理者通知] LINE push 失敗: {e}", flush=True)


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

    by_area, login_ok, maintenance = _run_async(_collect_records())
    area_names = [a["name"] for a in FACILITY_NAV[FACILITY_ID]["areas"]]

    # ── ログイン不可 ──────────────────────────
    if not login_ok:
        _set_output("deploy", "false")
        if maintenance:
            # メンテ時間は正常系。data.json維持・通知/デプロイなし・ジョブ成功扱い
            print("[メンテ] ふれあいネットのログイン機能停止中。data.json は維持して終了", flush=True)
            return 0
        # 真のログイン失敗（認証情報・サイト異常）→ 管理者通知＋ジョブ失敗
        _notify_admin("ログイン失敗（認証情報の確認、またはサイト構造変化の可能性）")
        return 1

    failed = [a for a in area_names if not _area_ok(by_area.get(a, []))]
    ok_areas = [a for a in area_names if a not in failed]

    # ── 全エリア失敗 ──────────────────────────
    # メンテ移行直後など。data.json は維持し、デプロイ/通知はスキップ（誤上書き防止）。
    if not ok_areas:
        print("[警告] 全エリア取得失敗（0件/全unknown）。data.json は維持してスキップ", flush=True)
        _notify_admin("全エリア取得失敗（メンテ移行 or サイト構造変化の可能性）")
        _set_output("deploy", "false")
        return 0

    # ── 成功エリアから payload を構築 ─────────────
    records = [r for a in ok_areas for r in by_area[a]]
    payload = _build_payload(records)

    # 失敗エリアは前回値で上書き（ユーザー表示の退行防止）し、差分通知からも除外
    if failed:
        _overlay_previous(payload, previous, failed)
        print(f"[部分失敗] {failed} は前回値を保持（通知対象外）", flush=True)
        _notify_admin(f"一部エリア取得失敗: {failed}（該当エリアは前回値を保持）")

    n_days = len(payload["days"])
    if n_days == 0:
        print("[警告] 取得0日。data.json は更新せずスキップ", flush=True)
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

    # ── 差分通知（取得に成功したエリアのみを母集合にする）──
    new_now = _reservable_keys(payload, only_areas=ok_areas)
    if previous is None:
        print("[通知] 前回データなしのため初回は通知しません", flush=True)
    else:
        old = _reservable_keys(previous, only_areas=ok_areas)
        added = {k: v for k, v in new_now.items() if k not in old}
        if added:
            print(f"[通知] 新着の予約可能枠 {len(added)}件", flush=True)
            _notify_line(added)
        else:
            print("[通知] 新着なし", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
