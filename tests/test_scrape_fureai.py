# scripts/scrape_fureai.py の純粋関数の単体テスト（Playwright 不要）
# 実行: py -m pytest tests/ -q
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_fureai as st  # noqa: E402


def _cfg(**kw):
    defaults = dict(
        facility_id="takatsu_sports_center",
        facility_name="高津スポーツセンター",
        notify_emoji="🏀",
        output_filename="data-test.json",
        web_id="takatsu",
        short_name="高津",
        short_area={"大体育室半面Ａ": "半面Ａ"},
    )
    defaults.update(kw)
    return st.FacilityConfig(**defaults)


def _rec(date, area, status, status_raw="", periods=None):
    return {
        "date": date,
        "area_name": area,
        "status": status,
        "status_raw": status_raw,
        "periods": periods or {},
    }


# ── _area_ok ──────────────────────────────
def test_area_ok_empty_is_ng():
    assert st._area_ok([]) is False


def test_area_ok_all_unknown_is_ng():
    recs = [_rec("2026-07-04", "A", "unknown") for _ in range(3)]
    assert st._area_ok(recs) is False


def test_area_ok_some_known_is_ok():
    recs = [_rec("2026-07-04", "A", "unknown"), _rec("2026-07-05", "A", "occupied")]
    assert st._area_ok(recs) is True


# ── _unknown_raw_ratio ────────────────────
def test_unknown_raw_ratio_no_raw():
    ratio, samples = st._unknown_raw_ratio([_rec("2026-07-04", "A", "unknown")])
    assert ratio == 0.0 and samples == []


def test_unknown_raw_ratio_detects_new_wording():
    recs = [
        _rec("2026-07-04", "A", "occupied", "予約あり"),
        _rec("2026-07-05", "A", "unknown", "新しい謎の文言"),
        _rec("2026-07-06", "A", "unknown", "新しい謎の文言"),
        _rec("2026-07-07", "A", "unknown", "別の文言"),
    ]
    ratio, samples = st._unknown_raw_ratio(recs)
    assert ratio == 0.75
    assert samples == ["別の文言", "新しい謎の文言"] or set(samples) == {"新しい謎の文言", "別の文言"}


# ── _build_payload / _validate_payload ────
def test_build_payload_and_validate_ok():
    cfg = _cfg()
    recs = [
        _rec("2026-07-04", "大体育室半面Ａ", "partial", "一部空き", {"午前": "available"}),
        _rec("2026-07-04", "大体育室半面Ｂ", "occupied", "予約あり"),
        _rec("2026-07-04", "小体育室", "available", "全て空き"),
    ]
    payload = st._build_payload(cfg, recs)
    assert payload["days"][0]["weekday"] == "土"
    assert payload["days"][0]["byArea"]["大体育室半面Ａ"]["periods"] == {"午前": "available"}
    errors = st._validate_payload(payload, payload["areas"], cfg.period_order)
    assert errors == []


def test_validate_payload_rejects_bad_status():
    cfg = _cfg()
    payload = st._build_payload(cfg, [_rec("2026-07-04", "大体育室半面Ａ", "occupied")])
    payload["days"][0]["byArea"]["大体育室半面Ａ"]["status"] = "banana"
    errors = st._validate_payload(payload, payload["areas"], cfg.period_order)
    assert any("banana" in e for e in errors)


def test_validate_payload_rejects_unknown_period_label():
    cfg = _cfg()
    recs = [_rec("2026-07-04", "大体育室半面Ａ", "partial", "一部空き", {"深夜": "available"})]
    payload = st._build_payload(cfg, recs)
    errors = st._validate_payload(payload, payload["areas"], cfg.period_order)
    assert any("深夜" in e for e in errors)


def test_validate_payload_rejects_empty_days():
    errors = st._validate_payload({"days": []}, ["A"], st.PERIOD_ORDER)
    assert errors == ["days が空"]


# ── _overlay_previous ─────────────────────
def test_overlay_previous_restores_failed_area():
    cfg = _cfg()
    payload = st._build_payload(cfg, [_rec("2026-07-04", "大体育室半面Ａ", "occupied")])
    previous = st._build_payload(
        cfg, [_rec("2026-07-04", "小体育室", "available", "全て空き", {"夜間": "available"})]
    )
    st._overlay_previous(payload, previous, ["小体育室"])
    assert payload["days"][0]["byArea"]["小体育室"]["status"] == "available"
    # 成功エリアは上書きされない
    assert payload["days"][0]["byArea"]["大体育室半面Ａ"]["status"] == "occupied"


# ── _reservable_keys / _notify_filter ─────
def test_reservable_keys_periods_and_day_level():
    cfg = _cfg()
    recs = [
        _rec("2026-07-04", "大体育室半面Ａ", "partial", "", {"午前": "available", "午後1": "occupied"}),
        _rec("2026-07-05", "小体育室", "available"),  # periods なし → 終日
    ]
    payload = st._build_payload(cfg, recs)
    keys = st._reservable_keys(payload)
    assert ("2026-07-04", "大体育室半面Ａ", "午前") in keys
    assert ("2026-07-04", "大体育室半面Ａ", "午後1") not in keys
    assert ("2026-07-05", "小体育室", "終日") in keys


def test_reservable_keys_only_areas_excludes_failed():
    cfg = _cfg()
    recs = [
        _rec("2026-07-04", "大体育室半面Ａ", "available"),
        _rec("2026-07-04", "小体育室", "available"),
    ]
    payload = st._build_payload(cfg, recs)
    keys = st._reservable_keys(payload, only_areas=["小体育室"])
    assert list(keys) == [("2026-07-04", "小体育室", "終日")]


def test_notify_filter_weekend_all_weekday_evening_only():
    keys = {
        ("2026-07-04", "A", "午前"): "土",   # 土曜 → 残る
        ("2026-07-06", "A", "午前"): "月",   # 平日昼 → 落ちる
        ("2026-07-06", "A", "夜間"): "月",   # 平日夜間 → 残る
        ("2026-07-06", "A", "18:30"): "月",  # テニス平日18:30 → 残る
    }
    out = st._notify_filter(keys)
    assert ("2026-07-04", "A", "午前") in out
    assert ("2026-07-06", "A", "午前") not in out
    assert ("2026-07-06", "A", "夜間") in out
    assert ("2026-07-06", "A", "18:30") in out


def test_build_payload_sets_holiday_flag():
    if st.jpholiday is None:
        import pytest
        pytest.skip("jpholiday 未インストール")
    cfg = _cfg()
    recs = [
        _rec("2026-07-20", "大体育室半面Ａ", "available"),  # 海の日（月曜）
        _rec("2026-07-21", "大体育室半面Ａ", "available"),  # 平日
    ]
    payload = st._build_payload(cfg, recs)
    flags = {d["date"]: d["isHoliday"] for d in payload["days"]}
    assert flags == {"2026-07-20": True, "2026-07-21": False}


def test_is_evening_label():
    assert st._is_evening_label("夜間") is True
    assert st._is_evening_label("18:30") is True
    assert st._is_evening_label("16:00") is False
    assert st._is_evening_label("午前") is False
