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
import re
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

try:
    import jpholiday  # 日本の祝日判定（LINE通知を土日祝に限定するため）
except ImportError:
    jpholiday = None

from dataclasses import dataclass, field  # noqa: E402
from backend.scrapers.fureai_net import (  # noqa: E402
    FureaiNetScraper,
    FACILITY_NAV,
    get_target_dates,
)

# ──────────────────────────────────────────
# 共通設定
# ──────────────────────────────────────────
BOOKING_TYPE = "reservation"
WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]
PERIOD_ORDER = ["午前", "午後1", "午後2", "夜間"]
RESERVABLE = ("available", "partial")  # 予約可能とみなす

APP_URL = "https://gym-getter.web.app"
# 予約申込みページ（施設トップではなく予約導線へ寄せる）
RESERVE_URL = "https://www.fureai-net.city.kawasaki.jp/web/"

LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


# ──────────────────────────────────────────
# スクレイプ対象の設定（単一の真実）
# 施設を増やすときは、まず backend/scrapers/fureai_net.py の FACILITY_NAV に
# 施設・エリアを定義し、ここ FACILITIES に1エントリ追加する。
# output_filename は web/public/ 配下に出力され、Web の施設タブが読む。
# ──────────────────────────────────────────
@dataclass
class FacilityConfig:
    facility_id: str          # FACILITY_NAV のキー
    facility_name: str        # 表示名・通知見出し
    notify_emoji: str         # LINE通知の絵文字
    output_filename: str      # web/public/ 配下の出力ファイル名
    short_area: dict = field(default_factory=dict)  # 通知用の短縮エリア名
    scrape_enabled: bool = True  # False ならスクレイプ対象外（UI/料金のみ先行追加した施設）
    # data.json の periodOrder。体育館は午前/午後…、テニスは開始時刻スロット。
    period_order: list = field(default_factory=lambda: list(PERIOD_ORDER))
    # 実行グループ。体育館は高頻度、テニスはコート数が多く重いので低頻度の別ワークフローで回す。
    # 環境変数 SCRAPE_GROUP（hall / tennis / all）で対象を絞る。
    group: str = "hall"

    @property
    def output_path(self) -> Path:
        return ROOT / "web" / "public" / self.output_filename

    @property
    def live_data_url(self) -> str:
        return f"{APP_URL}/{self.output_filename}"


FACILITIES: list[FacilityConfig] = [
    FacilityConfig(
        facility_id="takatsu_sports_center",
        facility_name="高津スポーツセンター",
        notify_emoji="🏀",
        output_filename="data.json",
        short_area={"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ", "小体育室": "小"},
    ),
    FacilityConfig(
        facility_id="culttz_kawasaki",
        facility_name="カルッツかわさき",
        notify_emoji="🏀",
        output_filename="data-culttz.json",
        short_area={"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ", "小体育室": "小"},
    ),
    FacilityConfig(
        facility_id="tama_sports_center",
        facility_name="多摩スポーツセンター（体育館）",
        notify_emoji="🏀",
        output_filename="data-tama.json",
        short_area={"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ"},
    ),
    FacilityConfig(
        facility_id="asao_sports_center",
        facility_name="麻生スポーツセンター",
        notify_emoji="🏀",
        output_filename="data-asao.json",
        short_area={"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ"},
    ),
    FacilityConfig(
        facility_id="saiwai_sports_center",
        facility_name="幸スポーツセンター",
        notify_emoji="🏀",
        output_filename="data-saiwai.json",
        short_area={"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ", "小体育室": "小"},
    ),
    FacilityConfig(
        facility_id="miyamae_sports_center",
        facility_name="宮前スポーツセンター",
        notify_emoji="🏀",
        output_filename="data-miyamae.json",
        short_area={"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ"},
    ),
    FacilityConfig(
        facility_id="kawasaki_civic_plaza",
        facility_name="川崎市民プラザ",
        notify_emoji="🏟",
        output_filename="data-plaza.json",
        short_area={"体育館": "体育館"},
    ),
    FacilityConfig(
        facility_id="todoroki_arena",
        facility_name="とどろきアリーナ",
        notify_emoji="🏟",
        output_filename="data-todoroki.json",
        short_area={
            "メインアリーナ": "メイン",
            "サブアリーナ": "サブ",
        },
    ),
    # 等々力テニスコート：時間貸し型でふれあいネットのページ形式が別。
    # 専用パーサ未実装のため当面スクレイプ対象外（UI/料金/タブのみ先行）。
    FacilityConfig(
        facility_id="todoroki_tennis",
        facility_name="等々力テニスコート",
        notify_emoji="🎾",
        output_filename="data-tennis.json",
        short_area={f"等々力テニス場{i}": str(i) for i in range(1, 11)},
        period_order=["9:00", "12:00", "14:00", "16:00", "18:30"],
        group="tennis",
    ),
    # 多摩スポーツセンター テニス場（時間貸し型・2面）
    FacilityConfig(
        facility_id="tama_tennis",
        facility_name="多摩スポーツセンター（テニス）",
        notify_emoji="🎾",
        output_filename="data-tama-tennis.json",
        short_area={"テニス場Ａ": "Ａ", "テニス場Ｂ": "Ｂ"},
        period_order=["9:00", "12:00", "14:00", "16:00", "18:30"],
        group="tennis",
    ),
    # 川崎マリエンテニスコート：時間貸し型（5スロット・12面）
    FacilityConfig(
        facility_id="marien_tennis",
        facility_name="川崎マリエンテニスコート",
        notify_emoji="🎾",
        output_filename="data-marien.json",
        short_area={f"テニスコート{i}": str(i) for i in range(1, 13)},
        period_order=["9:00", "12:00", "14:00", "16:00", "18:30"],
        group="tennis",
    ),
    # 大師テニスコート：時間貸し型（4スロット・3面・フラット料金）
    FacilityConfig(
        facility_id="daishi_tennis",
        facility_name="大師テニスコート",
        notify_emoji="🎾",
        output_filename="data-daishi.json",
        short_area={f"大師テニス場{i}": str(i) for i in range(1, 4)},
        period_order=["9:00", "12:00", "14:00", "16:00"],
        group="tennis",
    ),
    # 富士見テニスコート：時間貸し型（6スロット・12面・季節別料金）
    FacilityConfig(
        facility_id="fujimi_tennis",
        facility_name="富士見テニスコート",
        notify_emoji="🎾",
        output_filename="data-fujimi.json",
        short_area={f"富士見テニス場{i}": str(i) for i in range(1, 13)},
        period_order=["9:00", "11:00", "13:00", "15:00", "17:00", "18:30"],
        group="tennis",
    ),
]


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

async def _collect_records(cfg: FacilityConfig, scraper: FureaiNetScraper) -> dict:
    """cfg の全エリアを順にチェック（ログイン済みの scraper を渡す）。
    戻り値: by_area = { area_name: [日別レコード, ...] }（失敗エリアは [] か全unknown）
    """
    nav = FACILITY_NAV[cfg.facility_id]
    areas = nav["areas"]  # [{"name": ..., "index": ...}, ...]
    days_ahead = int(os.getenv("SCRAPE_DAYS_AHEAD", "30"))
    target_dates = get_target_dates(days_ahead)

    by_area: dict[str, list] = {}
    for area in areas:
        def on_progress(msg, _a=area["name"]):
            print(f"[{cfg.facility_name}/{_a}] {msg}", flush=True)

        try:
            recs = await scraper.check_area_days(
                cfg.facility_id,
                area["name"],
                area["index"],
                BOOKING_TYPE,
                target_dates,
                on_progress=on_progress,
            )
        except Exception as e:
            print(f"[{cfg.facility_name}/{area['name']}] 取得エラー: {e}", flush=True)
            recs = []
        print(f"[{cfg.facility_name}/{area['name']}] {len(recs)} 日取得", flush=True)
        by_area[area["name"]] = recs
    return by_area


def _area_ok(recs: list) -> bool:
    """エリアの取得が成功とみなせるか。
    0日 or 全て unknown は失敗扱い（ナビ失敗・構造変化のサイレント故障を検知）。"""
    if not recs:
        return False
    known = sum(1 for r in recs if r.get("status") not in ("unknown", "", None))
    return known > 0


def _build_payload(cfg: FacilityConfig, records: list[dict]) -> dict:
    """日別レコードを data.json の構造へ整形"""
    nav = FACILITY_NAV[cfg.facility_id]
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
        "facility": cfg.facility_name,
        "bookingType": BOOKING_TYPE,
        "reserveUrl": RESERVE_URL,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "areas": area_names,
        "periodOrder": cfg.period_order,
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

def _is_weekend_or_holiday(date_str: str) -> bool:
    """土日または日本の祝日なら True。LINE通知の対象判定に使う。
    jpholiday が無い環境では土日のみを対象にフォールバックする。"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    if d.weekday() >= 5:  # 月=0 … 土=5・日=6
        return True
    if jpholiday is not None:
        return bool(jpholiday.is_holiday(d))
    return False


def _is_evening_label(label: str) -> bool:
    """18時以降に始まる枠なら True。
    体育館は『夜間』(18:30-21:30)、テニスは時刻表記('18:30'等)で判定する。"""
    if label == "夜間":
        return True
    m = re.match(r"^(\d{1,2}):", label or "")
    if m:
        return int(m.group(1)) >= 18
    return False


def _notify_filter(keys: dict) -> dict:
    """LINE通知の対象枠だけを残す。
    - 土日祝の枠は全時間帯
    - 平日は18時以降に始まる枠（体育館『夜間』／テニス18:30〜）のみ
    """
    return {
        k: v
        for k, v in keys.items()
        if _is_weekend_or_holiday(k[0]) or _is_evening_label(k[2])
    }


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


def _clean_secret(s: str) -> str:
    """トークン等から前後空白と BOM(\\ufeff) を除去する。
    Windows で作成した Secret に BOM が混入すると、urllib が Authorization ヘッダを
    latin-1 でエンコードできず送信が丸ごと失敗する（.strip() は BOM を消さない）。"""
    return s.replace("﻿", "").strip()


def _fetch_previous(cfg: FacilityConfig) -> dict | None:
    """公開中の data.json（前回結果）を取得。失敗時は None。"""
    try:
        req = urllib.request.Request(cfg.live_data_url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        print(f"[通知] 前回データ取得不可（初回扱い）: {e}", flush=True)
        return None


def _format_keys(cfg: FacilityConfig, keys: dict) -> list:
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
        ordered = [l for l in cfg.period_order if l in labels]
        labels = ordered + [l for l in labels if l == "終日" and l not in ordered]
        short = cfg.short_area.get(area, area)
        out.append(f"・{md}({wd_map.get(date,'')}) {short} … {'・'.join(labels)}")
    return out


def _build_notify_message(cfg: FacilityConfig, current: dict, added: dict, removed: dict) -> str:
    """現在予約できる枠を全て日付順に並べた通知メッセージに整形する。
    変化（新たに空いた／埋まった）の件数をサマリとして添える。"""
    lines = [f"{cfg.notify_emoji} {cfg.facility_name} 予約できる枠（土日祝＋平日夜間）"]
    summary = []
    if added:
        summary.append(f"新たに{len(added)}枠")
    if removed:
        summary.append(f"埋まり{len(removed)}枠")
    if summary:
        lines.append("（" + " / ".join(summary) + "）")
    lines.append("")
    if current:
        lines += _format_keys(cfg, current)
    else:
        lines.append("・現在、予約できる枠はありません")
    lines.append("")
    lines.append(f"▶ {APP_URL}")
    return "\n".join(lines)[:1900]


def _notify_line(cfg: FacilityConfig, current: dict, added: dict, removed: dict) -> None:
    """現在予約できる枠の一覧を LINE（Messaging API ブロードキャスト）で通知する。
    LINE公式アカウントを友だち追加したメンバー全員に届く。"""
    token = _clean_secret(os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""))
    if not token:
        print("[通知] LINE_CHANNEL_ACCESS_TOKEN 未設定のため通知スキップ", flush=True)
        return

    content = _build_notify_message(cfg, current, added, removed)
    grouped_count = len({(d, a) for (d, a, _l) in current})
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

    token = _clean_secret(os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""))
    admin = _clean_secret(os.getenv("LINE_ADMIN_USER_ID", ""))
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


async def _collect_all(cfgs: list[FacilityConfig]) -> tuple[dict | None, bool]:
    """1回ログインして全施設を順に取得する（同一資格情報のため共有）。
    戻り値: (results, maintenance)
      results = { facility_id: by_area }。ログイン不可なら results=None。
    """
    results: dict[str, dict] = {}
    async with FureaiNetScraper(**_scraper_kwargs()) as scraper:
        if not await scraper.login():
            return None, scraper.maintenance
        for cfg in cfgs:
            if not cfg.scrape_enabled:
                continue
            print(f"--- {cfg.facility_name} 取得開始 ---", flush=True)
            results[cfg.facility_id] = await _collect_records(cfg, scraper)
    return results, False


def _process_facility(cfg: FacilityConfig, by_area: dict, previous: dict | None) -> bool:
    """1施設分の payload 構築・出力・通知を行う。デプロイ対象になったら True。"""
    area_names = [a["name"] for a in FACILITY_NAV[cfg.facility_id]["areas"]]
    failed = [a for a in area_names if not _area_ok(by_area.get(a, []))]
    ok_areas = [a for a in area_names if a not in failed]

    # 全エリア失敗（メンテ移行直後など）→ 既存ファイル維持・通知/デプロイなし
    if not ok_areas:
        print(f"[{cfg.facility_name}][警告] 全エリア取得失敗。{cfg.output_filename} は維持してスキップ", flush=True)
        _notify_admin(f"{cfg.facility_name}: 全エリア取得失敗（メンテ移行 or サイト構造変化の可能性）")
        return False

    records = [r for a in ok_areas for r in by_area[a]]
    payload = _build_payload(cfg, records)

    # 失敗エリアは前回値で上書き（表示の退行防止）し、差分通知からも除外
    if failed:
        _overlay_previous(payload, previous, failed)
        print(f"[{cfg.facility_name}][部分失敗] {failed} は前回値を保持（通知対象外）", flush=True)
        _notify_admin(f"{cfg.facility_name}: 一部エリア取得失敗 {failed}（該当エリアは前回値を保持）")

    n_days = len(payload["days"])
    if n_days == 0:
        print(f"[{cfg.facility_name}][警告] 取得0日。{cfg.output_filename} は更新せずスキップ", flush=True)
        return False

    cfg.output_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    n_reservable = sum(
        1
        for d in payload["days"]
        if any(a["status"] in RESERVABLE for a in d["byArea"].values())
    )
    print(
        f"=== {cfg.facility_name} 完了: {n_days}日 × {len(payload['areas'])}エリア / "
        f"予約可能{n_reservable}日 → {cfg.output_path} ===",
        flush=True,
    )

    # ── 差分通知（取得に成功したエリアのみを母集合にする）──
    # LINE通知は土日祝の全枠＋平日18時以降の枠を対象にする（平日昼間の変化は通知しない）。
    new_now = _notify_filter(_reservable_keys(payload, only_areas=ok_areas))
    if previous is None or not previous.get("days"):
        # 前回が無い or 空（プレースホルダ）の場合は初回扱い。
        # 有効化直後に全枠が「新着」として大量通知されるのを防ぐ。
        print(f"[{cfg.facility_name}][通知] 前回データなし/空のため初回は通知しません", flush=True)
    else:
        old = _notify_filter(_reservable_keys(previous, only_areas=ok_areas))
        added = {k: v for k, v in new_now.items() if k not in old}
        removed = {k: v for k, v in old.items() if k not in new_now}
        if added or removed:
            print(
                f"[{cfg.facility_name}][通知] 通知対象に変化あり（新着{len(added)}件 / 埋まり{len(removed)}件）。"
                f"現在の通知対象枠{len(new_now)}件を通知",
                flush=True,
            )
            _notify_line(cfg, new_now, added, removed)
        else:
            print(f"[{cfg.facility_name}][通知] 通知対象に変化なし", flush=True)
    return True


def main() -> int:
    print(f"=== ふれあいネット 空き確認 開始 {datetime.now():%Y-%m-%d %H:%M:%S} ===", flush=True)

    # 実行グループで対象を絞る（hall=体育館を高頻度 / tennis=テニスを低頻度の別ワークフロー）
    group = os.getenv("SCRAPE_GROUP", "all").strip().lower()
    enabled = [cfg for cfg in FACILITIES if cfg.scrape_enabled]
    if group in ("hall", "tennis"):
        targets = [cfg for cfg in enabled if cfg.group == group]
    else:
        targets = enabled
    non_targets = [cfg for cfg in FACILITIES if cfg not in targets]
    print(f"[対象] group={group} → {[c.facility_name for c in targets]}", flush=True)

    # 対象外の施設は公開中(ライブ)のデータを web/public へ書き戻し、デプロイで退行させない
    for cfg in non_targets:
        live = _fetch_previous(cfg)
        if live and live.get("days"):
            cfg.output_path.parent.mkdir(parents=True, exist_ok=True)
            cfg.output_path.write_text(
                json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    # 通知用に前回（公開中）データを施設ごとに先取得
    previous_map = {cfg.facility_id: _fetch_previous(cfg) for cfg in targets}

    collected, maintenance = _run_async(_collect_all(targets))

    # ── ログイン不可（全施設共通）──────────────
    if collected is None:
        _set_output("deploy", "false")
        if maintenance:
            # メンテ時間は正常系。各 data.json 維持・通知/デプロイなし・ジョブ成功扱い
            print("[メンテ] ふれあいネットのログイン機能停止中。各 data.json は維持して終了", flush=True)
            return 0
        # 真のログイン失敗（認証情報・サイト異常）→ 管理者通知＋ジョブ失敗
        _notify_admin("ログイン失敗（認証情報の確認、またはサイト構造変化の可能性）")
        return 1

    any_deploy = False
    for cfg in targets:
        if _process_facility(cfg, collected.get(cfg.facility_id, {}), previous_map[cfg.facility_id]):
            any_deploy = True

    # web 側のソース変更のみを反映したい場合は、データ無変化でも強制デプロイする。
    force = os.getenv("FORCE_DEPLOY", "").strip().lower() in ("1", "true", "yes")
    if force and not any_deploy:
        print("[デプロイ] FORCE_DEPLOY 指定によりデータ無変化でもデプロイします", flush=True)
        any_deploy = True

    _set_output("deploy", "true" if any_deploy else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
