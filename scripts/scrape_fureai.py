"""
高津スポーツセンター 空き確認（予約のみ）スタンドアロン スクレイパー

既存の FureaiNetScraper を再利用し、3エリア（大体育室半面Ａ/Ｂ・小体育室）の
予約空き状況を取得して web/public/data.json に書き出す。
DB・FastAPI には依存しない。

安定性（サイレント故障対策）:
  - ログイン失敗を「メンテ時間（正常）」と「真の失敗（要対応）」で区別する
  - エリア単位で成否を判定し、一部失敗時はそのエリアだけ前回値を保持・通知抑止する
  - 異常（真のログイン失敗・全エリア失敗・部分失敗）は管理者に通知し Actions に注釈を出す
  - デプロイ反映前に LINE 通知が飛んでデプロイ失敗時に連投されるのを防ぐため、
    --defer-notify（保留保存）と --flush-notify（デプロイ検証後に送信）の2段階送信に対応する
  - 改善ループを回せるよう、構造化実行ログを web/public/scrape-log.json に出力する

使い方:
    python scripts/scrape_fureai.py
    python scripts/scrape_fureai.py --defer-notify
    python scripts/scrape_fureai.py --flush-notify
    python scripts/scrape_fureai.py --notify-admin "エラーメッセージ"

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
import time
import argparse
import asyncio
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

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
# 予約申込みページ（簡易版ではなく高機能版）
RESERVE_URL = "https://www.fureai-net.city.kawasaki.jp/user/view/user/homeIndex.html"

LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_QUOTA_URL = "https://api.line.me/v2/bot/message/quota"
LINE_QUOTA_CONSUMPTION_URL = "https://api.line.me/v2/bot/message/quota/consumption"

# LINEのテキスト上限5000字に対する安全マージン
LINE_TEXT_LIMIT = 4800
# 1施設あたりの枠行数の上限（超過分は「…他N件」に畳む）
LINE_MAX_LINES_PER_FACILITY = 12

# 変化のあった施設を溜めて実行の最後に1通だけ送るためのキュー
_PENDING_NOTIFY: list[tuple] = []

# 管理者通知・警告メッセージを構造化ログにも残すためのバッファ
_ADMIN_WARNINGS: list[str] = []

# デプロイ成功後にLINE通知を送るための一時ファイル（ビルド対象外）
PENDING_NOTIFY_PATH = ROOT / ".pending_notify.json"
# GitHub Actions の Job Summary 貼付用ファイル
SCRAPE_SUMMARY_MD_PATH = ROOT / ".scrape_summary.md"
# フロントエンドから参照可能な構造化稼働ログ
SCRAPE_LOG_FILENAME = "scrape-log.json"
SCRAPE_LOG_PATH = ROOT / "web" / "public" / SCRAPE_LOG_FILENAME
LIVE_SCRAPE_LOG_URL = f"{APP_URL}/{SCRAPE_LOG_FILENAME}"

# map_status が返しうる全ステータス（web/src/lib/schema.ts の Zod enum と対）
KNOWN_STATUSES = {
    "available", "partial", "open", "occupied", "closed",
    "rain", "cancelling", "planned", "out_of_period", "unknown",
}

# status_raw があるのに unknown 判定になった割合がこの値以上なら管理者へ警告
# （ふれあいネットの文言変更を「取得0件」になる前に検知する）
UNKNOWN_ALERT_RATIO = float(os.getenv("UNKNOWN_ALERT_RATIO", "0.3"))


# ──────────────────────────────────────────
# 表示メタ（時間帯の利用時刻・使用料）
# フロントは facilities.json（本スクリプトが生成）経由でこれを読む。
# 施設定義の単一の真実は FACILITIES（＋fureai_net.py の FACILITY_NAV）。
# ──────────────────────────────────────────

# 体育館系（時間帯貸し）の利用時刻
HALL_PERIOD_TIMES = {
    "午前": "9:00〜12:00",
    "午後1": "12:10〜15:10",
    "午後2": "15:20〜18:20",
    "夜間": "18:30〜21:30",
}

# 料金定義の形式（フロントの priceFor が解釈する）:
#   match:    [{"contains": 部分一致文字列, "key": 料金区分キー}, ...]（先勝ち）
#   default:  match に当たらないときの料金区分キー
#   sets:     区分キー → {"weekday": {時間帯: 円}, "holiday": {時間帯: 円}}
#   seasonal: [{"period": 時間帯, "months": [対象月...], "yen": 円}]（該当月は上書き・任意）
# 金額は令和7年度・入場料を徴収しない場合の使用料。


def _flat_price(periods: list, yen_by_period: dict | int) -> dict:
    """平日土日同額のフラット料金。yen_by_period が int なら全スロット同額。"""
    if isinstance(yen_by_period, int):
        yen_by_period = {p: yen_by_period for p in periods}
    return {
        "match": [],
        "default": "コート",
        "sets": {"コート": {"weekday": yen_by_period, "holiday": yen_by_period}},
    }


# 高津・宮前・幸・麻生・多摩（体育館）共通：大体育室半面/小体育室
SPORTS_CENTER_PRICE = {
    "match": [{"contains": "小", "key": "小"}],
    "default": "半面",
    "sets": {
        "半面": {
            "weekday": {"午前": 3800, "午後1": 4530, "午後2": 5080, "夜間": 5800},
            "holiday": {"午前": 4560, "午後1": 5430, "午後2": 6090, "夜間": 6960},
        },
        "小": {
            "weekday": {"午前": 4230, "午後1": 4960, "午後2": 5560, "夜間": 6290},
            "holiday": {"午前": 5070, "午後1": 5950, "午後2": 6670, "夜間": 7540},
        },
    },
}

# カルッツかわさき：平日土日同額フラット
CULTTZ_PRICE = {
    "match": [{"contains": "小", "key": "小"}],
    "default": "半面",
    "sets": {
        "半面": {
            "weekday": {"午前": 4500, "午後1": 5400, "午後2": 6100, "夜間": 6900},
            "holiday": {"午前": 4500, "午後1": 5400, "午後2": 6100, "夜間": 6900},
        },
        "小": {
            "weekday": {"午前": 5600, "午後1": 6500, "午後2": 7300, "夜間": 8300},
            "holiday": {"午前": 5600, "午後1": 6500, "午後2": 7300, "夜間": 8300},
        },
    },
}

# 川崎市民プラザ 体育館（貸切）：平日は午前/夜間のみ設定
PLAZA_PRICE = {
    "match": [],
    "default": "体育館",
    "sets": {
        "体育館": {
            "weekday": {"午前": 5700, "夜間": 6500},
            "holiday": {"午前": 6800, "午後1": 6200, "午後2": 6200, "夜間": 7800},
        },
    },
}

# とどろきアリーナ：アマチュアスポーツ・入場料なし（サブは全面料金）
TODOROKI_PRICE = {
    "match": [
        {"contains": "メイン", "key": "メイン"},
        {"contains": "サブ", "key": "サブ"},
    ],
    "default": "体育室",
    "sets": {
        "メイン": {
            "weekday": {p: 20160 for p in PERIOD_ORDER},
            "holiday": {p: 24190 for p in PERIOD_ORDER},
        },
        "サブ": {
            "weekday": {p: 13440 for p in PERIOD_ORDER},
            "holiday": {p: 16120 for p in PERIOD_ORDER},
        },
        "体育室": {
            "weekday": {p: 3360 for p in PERIOD_ORDER},
            "holiday": {p: 4030 for p in PERIOD_ORDER},
        },
    },
}

# 富士見テニス：季節別料金（17:00 は 3〜9月 1,650円 / 15:00 は 11〜12月 2,800円）
FUJIMI_PRICE = {
    "match": [],
    "default": "コート",
    "sets": {
        "コート": {
            "weekday": {"9:00": 2200, "11:00": 2200, "13:00": 2200, "15:00": 2200, "17:00": 2550, "18:30": 3400},
            "holiday": {"9:00": 2200, "11:00": 2200, "13:00": 2200, "15:00": 2200, "17:00": 2550, "18:30": 3400},
        },
    },
    "seasonal": [
        {"period": "17:00", "months": [3, 4, 5, 6, 7, 8, 9], "yen": 1650},
        {"period": "15:00", "months": [11, 12], "yen": 2800},
    ],
}


# ──────────────────────────────────────────
# スクレイプ対象の設定（単一の真実）
# 施設を増やすときは、backend/scrapers/fureai_net.py の FACILITY_NAV に
# 施設・エリアを定義し、ここ FACILITIES に1エントリ追加する（この2箇所のみ）。
# Web のタブ・料金・時刻は facilities.json（本スクリプトが生成）経由で反映される。
# output_filename は web/public/ 配下に出力され、Web の施設タブが読む。
# ──────────────────────────────────────────
@dataclass
class FacilityConfig:
    facility_id: str          # FACILITY_NAV のキー
    facility_name: str        # 表示名・通知見出し
    notify_emoji: str         # LINE通知の絵文字
    output_filename: str      # web/public/ 配下の出力ファイル名
    web_id: str               # Web の施設タブ id（facilities.json に出力）
    short_name: str           # Web タブの短縮表示名
    short_area: dict = field(default_factory=dict)  # 通知・Web 表示の短縮エリア名
    scrape_enabled: bool = True  # False ならスクレイプ対象外（UI/料金のみ先行追加した施設）
    # data.json の periodOrder。体育館は午前/午後…、テニスは開始時刻スロット。
    period_order: list = field(default_factory=lambda: list(PERIOD_ORDER))
    # 時間帯ラベル → 利用時刻（Web 表示用）。体育館は HALL_PERIOD_TIMES。
    period_times: dict = field(default_factory=lambda: dict(HALL_PERIOD_TIMES))
    # 料金定義（上記の形式）。None なら料金非表示。
    prices: dict | None = None
    # 実行グループ 兼 Web の表示カテゴリ（hall / tennis）。
    # テニスはコート数が多く重いので低頻度の別ワークフローで回す。
    # 環境変数 SCRAPE_GROUP（hall / tennis / all）で対象を絞る。
    group: str = "hall"

    @property
    def output_path(self) -> Path:
        return ROOT / "web" / "public" / self.output_filename

    @property
    def live_data_url(self) -> str:
        return f"{APP_URL}/{self.output_filename}"


# 並び順は Web の施設タブ表示順（カテゴリ内で先頭がデフォルト選択）
FACILITIES: list[FacilityConfig] = [
    FacilityConfig(
        facility_id="takatsu_sports_center",
        facility_name="高津スポーツセンター",
        notify_emoji="🏀",
        output_filename="data.json",
        web_id="takatsu",
        short_name="高津",
        short_area={"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ", "小体育室": "小"},
        prices=SPORTS_CENTER_PRICE,
    ),
    FacilityConfig(
        facility_id="miyamae_sports_center",
        facility_name="宮前スポーツセンター",
        notify_emoji="🏀",
        output_filename="data-miyamae.json",
        web_id="miyamae",
        short_name="宮前",
        short_area={"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ"},
        prices=SPORTS_CENTER_PRICE,
    ),
    FacilityConfig(
        facility_id="saiwai_sports_center",
        facility_name="幸スポーツセンター",
        notify_emoji="🏀",
        output_filename="data-saiwai.json",
        web_id="saiwai",
        short_name="幸",
        short_area={"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ", "小体育室": "小"},
        prices=SPORTS_CENTER_PRICE,
    ),
    FacilityConfig(
        facility_id="asao_sports_center",
        facility_name="麻生スポーツセンター",
        notify_emoji="🏀",
        output_filename="data-asao.json",
        web_id="asao",
        short_name="麻生",
        short_area={"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ"},
        prices=SPORTS_CENTER_PRICE,
    ),
    FacilityConfig(
        facility_id="tama_sports_center",
        facility_name="多摩スポーツセンター（体育館）",
        notify_emoji="🏀",
        output_filename="data-tama.json",
        web_id="tama",
        short_name="多摩",
        short_area={"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ"},
        prices=SPORTS_CENTER_PRICE,
    ),
    FacilityConfig(
        facility_id="culttz_kawasaki",
        facility_name="カルッツかわさき",
        notify_emoji="🏀",
        output_filename="data-culttz.json",
        web_id="culttz",
        short_name="カルッツ",
        short_area={"大体育室半面Ａ": "半面Ａ", "大体育室半面Ｂ": "半面Ｂ", "小体育室": "小"},
        prices=CULTTZ_PRICE,
    ),
    FacilityConfig(
        facility_id="kawasaki_civic_plaza",
        facility_name="川崎市民プラザ",
        notify_emoji="🏟",
        output_filename="data-plaza.json",
        web_id="plaza",
        short_name="プラザ",
        short_area={"体育館": "体育館"},
        prices=PLAZA_PRICE,
    ),
    FacilityConfig(
        facility_id="todoroki_arena",
        facility_name="とどろきアリーナ",
        notify_emoji="🏟",
        output_filename="data-todoroki.json",
        web_id="todoroki",
        short_name="とどろき",
        short_area={
            "メインアリーナ": "メイン",
            "サブアリーナ": "サブ",
        },
        prices=TODOROKI_PRICE,
    ),
    # 等々力テニスコート（時間貸し型・10面）
    FacilityConfig(
        facility_id="todoroki_tennis",
        facility_name="等々力テニスコート",
        notify_emoji="🎾",
        output_filename="data-tennis.json",
        web_id="tennis",
        short_name="等々力",
        short_area={f"等々力テニス場{i}": str(i) for i in range(1, 11)},
        period_order=["9:00", "12:00", "14:00", "16:00", "18:30"],
        period_times={
            "9:00": "9:00〜12:00",
            "12:00": "12:00〜14:00",
            "14:00": "14:00〜16:00",
            "16:00": "16:00〜18:30",
            "18:30": "18:30〜20:30",
        },
        prices=_flat_price(
            ["9:00", "12:00", "14:00", "16:00", "18:30"],
            {"9:00": 2280, "12:00": 1520, "14:00": 1520, "16:00": 1900, "18:30": 1520},
        ),
        group="tennis",
    ),
    # 富士見テニスコート（時間貸し型・6スロット・12面・季節別料金）
    FacilityConfig(
        facility_id="fujimi_tennis",
        facility_name="富士見テニスコート",
        notify_emoji="🎾",
        output_filename="data-fujimi.json",
        web_id="fujimi",
        short_name="富士見",
        short_area={f"富士見テニス場{i}": str(i) for i in range(1, 13)},
        period_order=["9:00", "11:00", "13:00", "15:00", "17:00", "18:30"],
        period_times={
            "9:00": "9:00〜11:00",
            "11:00": "11:00〜13:00",
            "13:00": "13:00〜15:00",
            "15:00": "15:00〜17:00",
            "17:00": "17:00〜18:30",
            "18:30": "18:30〜20:30",
        },
        prices=FUJIMI_PRICE,
        group="tennis",
    ),
    # 大師テニスコート（時間貸し型・4スロット・3面・フラット料金）
    FacilityConfig(
        facility_id="daishi_tennis",
        facility_name="大師テニスコート",
        notify_emoji="🎾",
        output_filename="data-daishi.json",
        web_id="daishi",
        short_name="大師",
        short_area={f"大師テニス場{i}": str(i) for i in range(1, 4)},
        period_order=["9:00", "12:00", "14:00", "16:00"],
        period_times={
            "9:00": "9:00〜11:00",
            "12:00": "12:00〜14:00",
            "14:00": "14:00〜16:00",
            "16:00": "16:00〜18:00",
        },
        prices=_flat_price(["9:00", "12:00", "14:00", "16:00"], 1520),
        group="tennis",
    ),
    # 多摩スポーツセンター テニス場（時間貸し型・2面・個人利用料金）
    FacilityConfig(
        facility_id="tama_tennis",
        facility_name="多摩スポーツセンター（テニス）",
        notify_emoji="🎾",
        output_filename="data-tama-tennis.json",
        web_id="tamatennis",
        short_name="多摩",
        short_area={"テニス場Ａ": "Ａ", "テニス場Ｂ": "Ｂ"},
        period_order=["9:00", "12:00", "14:00", "16:00", "18:30"],
        period_times={
            "9:00": "9:00〜12:00",
            "12:00": "12:00〜14:00",
            "14:00": "14:00〜16:00",
            "16:00": "16:00〜18:00",
            "18:30": "18:30〜20:30",
        },
        prices=_flat_price(
            ["9:00", "12:00", "14:00", "16:00", "18:30"],
            {"9:00": 2700, "12:00": 1800, "14:00": 1800, "16:00": 1800, "18:30": 3720},
        ),
        group="tennis",
    ),
]

CATEGORIES = [
    {"id": "hall", "label": "体育館"},
    {"id": "tennis", "label": "テニス"},
]


def atomic_write_json(path: Path, data: dict) -> None:
    """一時ファイルに書き込んでからアトミックにリネーム置換することで、
    プロセス中断等によるファイル破損を防ぐ。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def build_facilities_index() -> dict:
    """フロントの施設タブ・料金・利用時刻の元データ（facilities.json）を組み立てる。"""
    return {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "categories": CATEGORIES,
        "facilities": [
            {
                "id": cfg.web_id,
                "name": cfg.facility_name,
                "short": cfg.short_name,
                "file": cfg.output_filename,
                "cat": cfg.group,
                "periodOrder": cfg.period_order,
                "periodTimes": cfg.period_times,
                "areaShort": cfg.short_area,
                "prices": cfg.prices,
            }
            for cfg in FACILITIES
        ],
    }


def write_facilities_index() -> Path:
    """web/public/facilities.json を生成する（ビルドに含まれフロントが読む）。"""
    path = ROOT / "web" / "public" / "facilities.json"
    atomic_write_json(path, build_facilities_index())
    print(f"[施設定義] facilities.json を生成 → {path}", flush=True)
    return path


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


def _unknown_raw_ratio(recs: list) -> tuple[float, list[str]]:
    """status_raw が取れているのに unknown 判定になったレコードの割合と、
    その生文字列サンプル（最大3種）を返す。map_status が知らない文言＝
    ふれあいネットの文言変更・構造変化の兆候。"""
    with_raw = [r for r in recs if r.get("status_raw")]
    if not with_raw:
        return 0.0, []
    unk = [r["status_raw"] for r in with_raw if r.get("status") == "unknown"]
    return len(unk) / len(with_raw), sorted(set(unk))[:3]


def _validate_payload(payload: dict, area_names: list[str], period_order: list[str]) -> list[str]:
    """書き込み前の最終スキーマ検証。フロントの Zod（schema.ts）と二重防御。
    エラー行のリストを返す（空なら正常）。最大10件で打ち切り。"""
    errors: list[str] = []
    days = payload.get("days") or []
    if not days:
        errors.append("days が空")

    if "updatedAt" in payload:
        updated_at = payload.get("updatedAt", "")
        try:
            datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            errors.append(f"不正な updatedAt: {updated_at!r}")

    for day in days:
        if len(errors) >= 10:
            break
        date = day.get("date", "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            errors.append(f"不正な日付: {date!r}")
            continue
        try:
            d = datetime.strptime(date, "%Y-%m-%d")
            expected_wd = WEEKDAYS_JA[d.weekday()]
            if day.get("weekday") != expected_wd:
                errors.append(f"{date}: 不正な曜日 {day.get('weekday')!r} (期待値: {expected_wd})")
        except ValueError:
            errors.append(f"存在しない日付: {date!r}")

        if not isinstance(day.get("isHoliday"), bool):
            errors.append(f"{date}: isHoliday が真偽値でない ({type(day.get('isHoliday')).__name__})")

        by_area = day.get("byArea") or {}
        if set(by_area) != set(area_names):
            errors.append(f"{date}: エリアキー不一致 {sorted(by_area)}")
        for area, a in by_area.items():
            if not isinstance(a, dict):
                errors.append(f"{date}/{area}: エリアデータが辞書型でない")
                continue
            if a.get("status") not in KNOWN_STATUSES:
                errors.append(f"{date}/{area}: 不正な status {a.get('status')!r}")
            for p, st in (a.get("periods") or {}).items():
                if st not in KNOWN_STATUSES:
                    errors.append(f"{date}/{area}/{p}: 不正な period status {st!r}")
                if p not in period_order:
                    errors.append(f"{date}/{area}: periodOrder に無いラベル {p!r}")
    return errors[:10]


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
            # 祝日フラグ（土日は weekday で判定）。フロントの料金・フィルタ・日付色に使う。
            "isHoliday": _is_jp_holiday(date),
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

def _is_jp_holiday(date_str: str) -> bool:
    """日本の祝日なら True（土日は含まない）。jpholiday が無い環境では常に False。"""
    if jpholiday is None:
        return False
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    return bool(jpholiday.is_holiday(d))


def _is_weekend_or_holiday(date_str: str) -> bool:
    """土日または日本の祝日なら True。LINE通知の対象判定に使う。
    jpholiday が無い環境では土日のみを対象にフォールバックする。"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    if d.weekday() >= 5:  # 月=0 … 土=5・日=6
        return True
    return _is_jp_holiday(date_str)


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
    return s.replace("\ufeff", "").strip()


def _http_request_with_retry(
    req: urllib.request.Request, timeout: int = 15, max_retries: int = 3
) -> bytes:
    """urllib リクエストを指数バックオフ付きでリトライする。"""
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            # 4xx（404等）は再試行しても無駄なため即時 raise
            if 400 <= e.code < 500 and e.code != 429:
                raise
            last_err = e
        except Exception as e:
            last_err = e

        if attempt < max_retries:
            time.sleep(attempt * 1.5)

    if last_err:
        raise last_err
    raise RuntimeError("リクエストが完了しませんでした")


def _fetch_previous(cfg: FacilityConfig) -> dict | None:
    """公開中の data.json（前回結果）を取得。CDN障害時はローカル既存ファイルをフォールバックとする。"""
    try:
        req = urllib.request.Request(cfg.live_data_url, headers={"Cache-Control": "no-cache"})
        data = _http_request_with_retry(req, timeout=15, max_retries=3)
        return json.loads(data.decode("utf-8"))
    except Exception as e:
        print(f"[通知] ライブデータ取得失敗 ({cfg.facility_name}): {e}", flush=True)

    # CDN等から取得できない場合、ローカルに健全な既存ファイルがあればフォールバックとして採用
    if cfg.output_path.exists():
        try:
            local_data = json.loads(cfg.output_path.read_text(encoding="utf-8"))
            if local_data.get("days"):
                print(f"[通知] ローカル既存ファイルを前回値として利用: {cfg.output_path}", flush=True)
                return local_data
        except Exception as le:
            print(f"[通知] ローカル既存ファイルの読み込み失敗: {le}", flush=True)

    print(f"[通知] {cfg.facility_name} 前回データ取得不可（初回扱い）", flush=True)
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


def _build_facility_section(
    cfg: FacilityConfig, current: dict, added: dict, removed: dict, max_lines: int
) -> list[str]:
    """1施設分の通知セクションを行リストで返す。
    max_lines を超える枠行は「…他N件」に畳んで全体の文字数を抑える。"""
    lines = [f"{cfg.notify_emoji} {cfg.facility_name}"]
    summary = []
    if added:
        summary.append(f"新たに{len(added)}枠")
    if removed:
        summary.append(f"埋まり{len(removed)}枠")
    if summary:
        lines.append("（" + " / ".join(summary) + "）")
    if current:
        rows = _format_keys(cfg, current)
        if len(rows) > max_lines:
            rows = rows[:max_lines] + [f"・…他{len(rows) - max_lines}件"]
        lines += rows
    else:
        lines.append("・現在、予約できる枠はありません")
    # 通知タップで該当施設のタブが開くよう ?f= を付ける
    lines.append(f"▶ {APP_URL}/?f={cfg.web_id}")
    return lines


def _build_combined_message(entries: list) -> str:
    """変化のあった全施設を1通分のテキストに連結する。
    上限に収まらない場合は施設ごとの枠行を段階的に畳んで収める。"""
    head = f"🔔 空き枠の変化（{len(entries)}施設 / 土日祝＋平日夜間）"
    text = ""
    for max_lines in (LINE_MAX_LINES_PER_FACILITY, 6, 3, 1):
        blocks = [
            "\n".join(_build_facility_section(cfg, cur, add, rem, max_lines))
            for cfg, cur, add, rem in entries
        ]
        text = head + "\n\n" + "\n\n".join(blocks)
        if len(text) <= LINE_TEXT_LIMIT:
            return text
    return text[:LINE_TEXT_LIMIT]


def _queue_notify(cfg: FacilityConfig, current: dict, added: dict, removed: dict) -> None:
    """変化のあった施設を通知キューに積む。送信は _flush_notify() が1通にまとめて行う。"""
    _PENDING_NOTIFY.append((cfg, current, added, removed))


def _save_pending_notify(path: Path = PENDING_NOTIFY_PATH) -> bool:
    """保留中の通知をファイルに保存する（デプロイ反映確認後に --flush-notify で送るため）。
    保存した場合は True、通知対象が無い場合は既存ファイルを消して False を返す。"""
    if not _PENDING_NOTIFY:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
        print("[通知] 変化なし（保留通知ファイルなし）", flush=True)
        return False

    content = _build_combined_message(_PENDING_NOTIFY)
    names = "／".join(cfg.facility_name for cfg, _c, _a, _r in _PENDING_NOTIFY)
    data = {
        "createdAt": datetime.now(JST).isoformat(timespec="seconds"),
        "facilityCount": len(_PENDING_NOTIFY),
        "facilityNames": names,
        "text": content,
    }
    atomic_write_json(path, data)
    print(
        f"[通知] デプロイ後に送信するため通知を保留保存 ({len(_PENDING_NOTIFY)}施設: {names} / {len(content)}字) → {path}",
        flush=True,
    )
    _PENDING_NOTIFY.clear()
    return True


def _send_broadcast_text(content: str, count: int, names: str) -> bool:
    """整形済みテキストを LINE Broadcast API へ送信する。"""
    token = _clean_secret(os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""))
    if not token:
        print("[通知] LINE_CHANNEL_ACCESS_TOKEN 未設定のため通知スキップ", flush=True)
        return False

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
        _http_request_with_retry(req, timeout=15, max_retries=2)
        print(
            f"[通知] LINE送信OK（1通に集約 / {count}施設: {names} / {len(content)}字）",
            flush=True,
        )
        _check_line_quota(token)
        return True
    except Exception as e:
        body = ""
        if isinstance(e, urllib.error.HTTPError):
            try:
                body = e.read().decode("utf-8")[:200]
            except Exception:
                pass
        print(f"[通知] LINE送信失敗: {e} {body}", flush=True)
        return False


def _flush_notify() -> None:
    """キューに積まれた全施設の変化を LINE へ1通だけブロードキャストする。
    施設ごとに送ると最大12通/実行になり、無料枠（200通/月・友だち数×通数で消費）を
    すぐ使い切るため、1回の実行につき必ず1通へ集約する。"""
    if not _PENDING_NOTIFY:
        return

    content = _build_combined_message(_PENDING_NOTIFY)
    names = "／".join(cfg.facility_name for cfg, _c, _a, _r in _PENDING_NOTIFY)
    count = len(_PENDING_NOTIFY)
    try:
        _send_broadcast_text(content, count, names)
    finally:
        _PENDING_NOTIFY.clear()


def flush_saved_notify(path: Path = PENDING_NOTIFY_PATH) -> int:
    """--flush-notify 用：保存済みの保留通知ファイルを読み込んで LINE 送信する。"""
    if not path.exists():
        print("[通知] 保留通知ファイルが無いため送信スキップ", flush=True)
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        text = data.get("text", "")
        count = int(data.get("facilityCount") or 0)
        names = str(data.get("facilityNames") or "")
        if not text:
            print("[通知] 保留通知が空のためスキップ", flush=True)
            return 0
        _send_broadcast_text(text, count, names)
        return 0
    except Exception as e:
        print(f"[通知] 保留通知の読み込み/送信エラー: {e}", flush=True)
        return 1
    finally:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass


def get_line_quota_info() -> dict | None:
    """LINE Messaging API の月間送信数と上限を取得して辞書で返す（取得不可時は None）。"""
    token = _clean_secret(os.getenv("LINE_CHANNEL_ACCESS_TOKEN", ""))
    if not token:
        return None
    try:
        def _get(url: str) -> dict:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            data = _http_request_with_retry(req, timeout=10, max_retries=2)
            return json.loads(data.decode("utf-8"))

        quota = _get(LINE_QUOTA_URL)
        usage = int(_get(LINE_QUOTA_CONSUMPTION_URL).get("totalUsage") or 0)
        limit = int(quota.get("value") or 0) if quota.get("type") == "limited" else None
        return {"usage": usage, "limit": limit, "type": quota.get("type", "unknown")}
    except Exception as e:
        print(f"[通知] LINE送信数の確認失敗: {e}", flush=True)
        return None


def _check_line_quota(token: str) -> None:
    """LINE Messaging API の月間送信数を照会し、無料枠の90%以上を消費していたら
    管理者へ警告する（超過すると利用者への通知が止まるため、その前に気づく）。
    broadcast は友だち数×1通で消費されるため、実測値の照会が唯一正確。"""
    try:
        def _get(url: str) -> dict:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            data = _http_request_with_retry(req, timeout=15, max_retries=2)
            return json.loads(data.decode("utf-8"))

        quota = _get(LINE_QUOTA_URL)
        if quota.get("type") != "limited":
            return  # 従量プラン等、上限なし
        limit = int(quota.get("value") or 0)
        usage = int(_get(LINE_QUOTA_CONSUMPTION_URL).get("totalUsage") or 0)
        print(f"[通知] LINE送信数: 今月 {usage}/{limit} 通", flush=True)
        if limit and usage >= limit * 0.9:
            _notify_admin(
                f"LINE無料枠が残りわずか（今月 {usage}/{limit} 通）。"
                "超過すると利用者への通知が届かなくなります"
            )
    except Exception as e:
        print(f"[通知] LINE送信数の確認失敗: {e}", flush=True)


def _notify_admin(reason: str) -> None:
    """管理者向けの異常アラート。GitHub Actions の注釈を必ず出し、
    LINE_ADMIN_USER_ID が設定されていれば管理者個人へ push する。"""
    msg = f"⚠ GYM Getter 異常: {reason}"
    _ADMIN_WARNINGS.append(reason)
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
        _http_request_with_retry(req, timeout=15, max_retries=2)
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


async def _collect_all(cfgs: list[FacilityConfig]) -> tuple[dict | None, bool, list[dict]]:
    """1回ログインして全施設を順に取得する（同一資格情報のため共有）。
    戻り値: (results, maintenance, flow_log)
      results = { facility_id: by_area }。ログイン不可なら results=None。
    """
    results: dict[str, dict] = {}
    async with FureaiNetScraper(**_scraper_kwargs()) as scraper:
        if not await scraper.login():
            return None, scraper.maintenance, list(scraper._flow_log)
        for cfg in cfgs:
            if not cfg.scrape_enabled:
                continue
            print(f"--- {cfg.facility_name} 取得開始 ---", flush=True)
            results[cfg.facility_id] = await _collect_records(cfg, scraper)
        return results, False, list(scraper._flow_log)


def _process_facility(
    cfg: FacilityConfig,
    by_area: dict,
    previous: dict | None,
    diag_out: dict | None = None,
) -> bool:
    """1施設分の payload 構築・出力・通知を行う。デプロイ対象になったら True。
    diag_out に辞書を渡すと、診断ログ用の結果メタを格納する。"""
    area_names = [a["name"] for a in FACILITY_NAV[cfg.facility_id]["areas"]]
    failed = [a for a in area_names if not _area_ok(by_area.get(a, []))]
    ok_areas = [a for a in area_names if a not in failed]

    if diag_out is not None:
        diag_out.update({
            "id": cfg.web_id,
            "name": cfg.facility_name,
            "status": "failed" if not ok_areas else ("partial" if failed else "ok"),
            "days": 0,
            "reservableDays": 0,
            "okAreas": ok_areas,
            "failedAreas": failed,
            "unknownRatio": 0.0,
            "unknownSamples": [],
            "notifyAdded": 0,
            "notifyRemoved": 0,
        })

    # 全エリア失敗（メンテ移行直後など）→ 既存ファイル維持・通知/デプロイなし
    if not ok_areas:
        print(f"[{cfg.facility_name}][警告] 全エリア取得失敗。{cfg.output_filename} は維持してスキップ", flush=True)
        _notify_admin(f"{cfg.facility_name}: 全エリア取得失敗（メンテ移行 or サイト構造変化の可能性）")
        return False

    # 文言変更の早期検知：生文字列があるのに unknown 判定が多いエリアは構造変化の疑い
    max_unk_ratio = 0.0
    unk_samples: list[str] = []
    for a in ok_areas:
        ratio, samples = _unknown_raw_ratio(by_area[a])
        if ratio > max_unk_ratio:
            max_unk_ratio = ratio
            unk_samples = samples
        if ratio >= UNKNOWN_ALERT_RATIO:
            _notify_admin(
                f"{cfg.facility_name}/{a}: 未知のステータス文言が{ratio:.0%}"
                f"（例: {samples}）。ふれあいネットの文言変更の可能性"
            )
    if diag_out is not None:
        diag_out["unknownRatio"] = round(max_unk_ratio, 3)
        diag_out["unknownSamples"] = unk_samples

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
        if diag_out is not None:
            diag_out["status"] = "failed"
        return False

    # 書き込み前の最終スキーマ検証（壊れた JSON をデプロイしない最後の砦）
    schema_errors = _validate_payload(payload, area_names, cfg.period_order)
    if schema_errors:
        print(f"[{cfg.facility_name}][警告] スキーマ検証エラーのため更新スキップ: {schema_errors}", flush=True)
        _notify_admin(f"{cfg.facility_name}: data検証エラーで更新スキップ {schema_errors[:3]}")
        if diag_out is not None:
            diag_out["status"] = "failed"
        return False

    atomic_write_json(cfg.output_path, payload)

    n_reservable = sum(
        1
        for d in payload["days"]
        if any(a["status"] in RESERVABLE for a in d["byArea"].values())
    )
    if diag_out is not None:
        diag_out["days"] = n_days
        diag_out["reservableDays"] = n_reservable

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
        if diag_out is not None:
            diag_out["notifyAdded"] = len(added)
            diag_out["notifyRemoved"] = len(removed)
        if added or removed:
            print(
                f"[{cfg.facility_name}][通知] 通知対象に変化あり（新着{len(added)}件 / 埋まり{len(removed)}件）。"
                f"現在の通知対象枠{len(new_now)}件を通知",
                flush=True,
            )
            _queue_notify(cfg, new_now, added, removed)
        else:
            print(f"[{cfg.facility_name}][通知] 通知対象に変化なし", flush=True)
    return True


def _fetch_previous_scrape_log() -> dict:
    """公開中またはローカルの scrape-log.json を取得する（別グループの結果を保持するため）。"""
    try:
        req = urllib.request.Request(LIVE_SCRAPE_LOG_URL, headers={"Cache-Control": "no-cache"})
        raw = _http_request_with_retry(req, timeout=10, max_retries=2)
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and isinstance(data.get("groups"), dict):
            return data
    except Exception:
        pass
    if SCRAPE_LOG_PATH.exists():
        try:
            data = json.loads(SCRAPE_LOG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("groups"), dict):
                return data
        except Exception:
            pass
    return {"updatedAt": datetime.now(timezone.utc).isoformat(), "groups": {}}


def write_scrape_log_and_summary(
    group: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    maintenance: bool,
    facility_diags: list[dict],
    flow_log: list[dict],
    warnings: list[str],
    line_quota: dict | None,
) -> None:
    """構造化稼働ログ web/public/scrape-log.json と GitHub Actions 用サマリ .scrape_summary.md を出力する。"""
    duration_sec = round((finished_at - started_at).total_seconds(), 1)
    run_id = os.getenv("GITHUB_RUN_ID", "")
    group_entry = {
        "group": group,
        "startedAt": started_at.isoformat(timespec="seconds"),
        "finishedAt": finished_at.isoformat(timespec="seconds"),
        "durationSec": duration_sec,
        "status": status,
        "maintenance": maintenance,
        "runId": run_id,
        "lineQuota": line_quota,
        "facilities": facility_diags,
        "warnings": warnings[:20],
        "flowLogTail": [
            f"{item.get('time', '')} {item.get('msg', '')}"
            for item in flow_log[-30:]
        ],
    }

    prev_log = _fetch_previous_scrape_log()
    groups = prev_log.get("groups") if isinstance(prev_log.get("groups"), dict) else {}
    groups[group] = group_entry
    merged_log = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "groups": groups,
    }
    atomic_write_json(SCRAPE_LOG_PATH, merged_log)
    print(f"[稼働ログ] {SCRAPE_LOG_FILENAME} を更新 → {SCRAPE_LOG_PATH}", flush=True)

    # GitHub Actions の Job Summary にそのまま貼れる Markdown を生成
    lines = [
        f"=== GYM Getter 実行ログ ({group}) ===",
        f"実行時刻(JST): {started_at:%Y-%m-%d %H:%M:%S} -> {finished_at:%H:%M:%S} ({duration_sec}s)",
        f"ステータス: {status} (maintenance={maintenance}, runId={run_id or 'local'})",
    ]
    if line_quota:
        lim = line_quota.get("limit") or "unlimited"
        lines.append(f"LINE送信数: 今月 {line_quota.get('usage', 0)}/{lim} 通")
    if warnings:
        lines.append("警告/異常:")
        for w in warnings:
            lines.append(f"  - {w}")
    if facility_diags:
        lines.append("施設別結果:")
        for fd in facility_diags:
            unk_str = f" / unk={fd['unknownRatio']:.0%}" if fd.get("unknownRatio", 0) > 0 else ""
            fail_str = f" / 失敗エリア={fd['failedAreas']}" if fd.get("failedAreas") else ""
            diff_str = ""
            if fd.get("notifyAdded") or fd.get("notifyRemoved"):
                diff_str = f" / 通知差分(+{fd['notifyAdded']}, -{fd['notifyRemoved']})"
            lines.append(
                f"  - {fd['name']}: {fd['status']} ({fd['days']}日, 予約可{fd['reservableDays']}日{unk_str}{fail_str}{diff_str})"
            )
    if flow_log:
        lines.append("スクレイパーログ末尾:")
        for item in flow_log[-10:]:
            lines.append(f"  {item.get('time', '')} {item.get('msg', '')}")

    try:
        SCRAPE_SUMMARY_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ふれあいネット空き確認スクレイパー")
    parser.add_argument(
        "--defer-notify",
        action="store_true",
        help="LINEブロードキャストを即時送信せず .pending_notify.json に保存する（デプロイ反映後に --flush-notify で送信）",
    )
    parser.add_argument(
        "--flush-notify",
        action="store_true",
        help="保存済みの .pending_notify.json を読み込んで LINE ブロードキャストを送信する",
    )
    parser.add_argument(
        "--notify-admin",
        type=str,
        default="",
        help="管理者へ異常アラート（GitHub Actions 注釈 + LINE push）を送信して終了する",
    )
    args = parser.parse_args(argv)

    if args.notify_admin:
        _notify_admin(args.notify_admin)
        return 0

    if args.flush_notify:
        return flush_saved_notify()

    defer_notify = args.defer_notify or (
        os.getenv("DEFER_NOTIFY", "").strip().lower() in ("1", "true", "yes")
    )

    started_at = datetime.now(JST)
    _ADMIN_WARNINGS.clear()
    print(f"=== ふれあいネット 空き確認 開始 {started_at:%Y-%m-%d %H:%M:%S} (JST) ===", flush=True)

    # 施設タブ・料金・時刻のメタをフロントへ渡す（毎回生成＝FACILITIES が単一の真実）
    write_facilities_index()

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
            atomic_write_json(cfg.output_path, live)

    # 通知用に前回（公開中）データを施設ごとに先取得
    previous_map = {cfg.facility_id: _fetch_previous(cfg) for cfg in targets}

    collected_res = _run_async(_collect_all(targets))
    # 後方互換（テスト等で2タプルが返る場合にも対応）
    if len(collected_res) == 3:
        collected, maintenance, flow_log = collected_res
    else:
        collected, maintenance = collected_res
        flow_log = []

    force = os.getenv("FORCE_DEPLOY", "").strip().lower() in ("1", "true", "yes")
    line_quota = get_line_quota_info()

    # ── ログイン不可（全施設共通）──────────────
    if collected is None:
        finished_at = datetime.now(JST)
        if maintenance:
            write_scrape_log_and_summary(
                group=group,
                started_at=started_at,
                finished_at=finished_at,
                status="maintenance",
                maintenance=True,
                facility_diags=[],
                flow_log=flow_log,
                warnings=list(_ADMIN_WARNINGS),
                line_quota=line_quota,
            )
            # メンテ時間は正常系。各 data.json 維持・通知なし・ジョブ成功扱い。
            # ただし FORCE_DEPLOY 指定時は、対象施設も公開中(ライブ)データを書き戻して
            # 退行を防いだ上でデプロイし、web 側のソース変更だけを反映する。
            if force:
                for cfg in targets:
                    live = previous_map.get(cfg.facility_id)
                    if live and live.get("days"):
                        atomic_write_json(cfg.output_path, live)
                print("[メンテ] FORCE_DEPLOY 指定：ライブデータを書き戻してデプロイします", flush=True)
                _set_output("deploy", "true")
                return 0
            _set_output("deploy", "false")
            print("[メンテ] ふれあいネットのログイン機能停止中。各 data.json は維持して終了", flush=True)
            return 0
        _set_output("deploy", "false")
        # 真のログイン失敗（認証情報・サイト異常）→ 管理者通知＋ジョブ失敗
        _notify_admin("ログイン失敗（認証情報の確認、またはサイト構造変化の可能性）")
        write_scrape_log_and_summary(
            group=group,
            started_at=started_at,
            finished_at=finished_at,
            status="error",
            maintenance=False,
            facility_diags=[],
            flow_log=flow_log,
            warnings=list(_ADMIN_WARNINGS),
            line_quota=line_quota,
        )
        return 1

    any_deploy = False
    facility_diags: list[dict] = []
    for cfg in targets:
        f_diag: dict = {}
        if _process_facility(cfg, collected.get(cfg.facility_id, {}), previous_map[cfg.facility_id], diag_out=f_diag):
            any_deploy = True
        if f_diag:
            facility_diags.append(f_diag)

    # デプロイ成功前に通知を送るとデプロイ失敗時に連投されるため、
    # CI実行時（--defer-notify）はファイルに保留保存し、デプロイ検証成功後に --flush-notify で送る。
    if defer_notify:
        _save_pending_notify()
    else:
        _flush_notify()

    # web 側のソース変更のみを反映したい場合は、データ無変化でも強制デプロイする。
    if force and not any_deploy:
        print("[デプロイ] FORCE_DEPLOY 指定によりデータ無変化でもデプロイします", flush=True)
        any_deploy = True

    overall_status = "ok"
    if any(d.get("status") != "ok" for d in facility_diags):
        overall_status = "partial" if any_deploy else "error"

    write_scrape_log_and_summary(
        group=group,
        started_at=started_at,
        finished_at=datetime.now(JST),
        status=overall_status,
        maintenance=False,
        facility_diags=facility_diags,
        flow_log=flow_log,
        warnings=list(_ADMIN_WARNINGS),
        line_quota=line_quota,
    )

    _set_output("deploy", "true" if any_deploy else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
