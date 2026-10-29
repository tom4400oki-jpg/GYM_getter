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


# ── 安定性強化テスト ────────────────────────
def test_atomic_write_json_creates_valid_file(tmp_path):
    target = tmp_path / "test_data.json"
    data = {"status": "ok", "count": 42}
    st.atomic_write_json(target, data)
    assert target.exists()
    import json
    assert json.loads(target.read_text(encoding="utf-8")) == data
    # 一時ファイルが残っていないこと
    assert len(list(tmp_path.glob("*.tmp.*"))) == 0


def test_validate_payload_rejects_bad_updated_at():
    cfg = _cfg()
    payload = st._build_payload(cfg, [_rec("2026-07-04", "大体育室半面Ａ", "occupied")])
    payload["updatedAt"] = "invalid-date"
    errors = st.validate_payload(payload, payload["areas"], cfg.period_order) if hasattr(st, "validate_payload") else st._validate_payload(payload, payload["areas"], cfg.period_order)
    assert any("updatedAt" in e for e in errors)


def test_validate_payload_rejects_weekday_mismatch():
    cfg = _cfg()
    payload = st._build_payload(cfg, [_rec("2026-07-04", "大体育室半面Ａ", "occupied")])
    # 2026-07-04 は土曜日だが「日」に改ざん
    payload["days"][0]["weekday"] = "日"
    errors = st._validate_payload(payload, payload["areas"], cfg.period_order)
    assert any("不正な曜日" in e for e in errors)


def test_validate_payload_rejects_non_bool_holiday():
    cfg = _cfg()
    payload = st._build_payload(cfg, [_rec("2026-07-04", "大体育室半面Ａ", "occupied")])
    payload["days"][0]["isHoliday"] = "yes"  # 文字列
    errors = st._validate_payload(payload, payload["areas"], cfg.period_order)
    assert any("isHoliday" in e for e in errors)


def test_validate_payload_rejects_non_dict_area():
    cfg = _cfg()
    payload = st._build_payload(cfg, [_rec("2026-07-04", "大体育室半面Ａ", "occupied")])
    payload["days"][0]["byArea"]["大体育室半面Ａ"] = "occupied"  # dict ではなく文字列
    errors = st._validate_payload(payload, payload["areas"], cfg.period_order)
    assert any("辞書型でない" in e for e in errors)


def test_http_request_with_retry_succeeds(monkeypatch):
    import io
    import urllib.request

    class MockResponse:
        def read(self):
            return b'{"ok": true}'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=15: MockResponse())
    req = urllib.request.Request("http://example.com")
    res = st._http_request_with_retry(req, timeout=5, max_retries=2)
    assert res == b'{"ok": true}'


def test_save_and_flush_pending_notify(tmp_path, monkeypatch):
    cfg = _cfg()
    pending_file = tmp_path / ".pending_notify.json"
    st._PENDING_NOTIFY.clear()

    # 通知対象が無い時はファイルを作らず False
    assert st._save_pending_notify(pending_file) is False
    assert not pending_file.exists()

    # 通知対象をキューに入れて保留保存
    cur = {("2026-07-04", "大体育室半面Ａ", "午前"): "土"}
    st._queue_notify(cfg, cur, cur, {})
    assert st._save_pending_notify(pending_file) is True
    assert pending_file.exists()
    assert len(st._PENDING_NOTIFY) == 0

    # flush_saved_notify で送信関数が呼ばれ、ファイルが消えること
    sent = []
    monkeypatch.setattr(
        st,
        "_send_broadcast_text",
        lambda content, count, names: sent.append((content, count, names)) or True,
    )
    rc = st.flush_saved_notify(pending_file)
    assert rc == 0
    assert len(sent) == 1
    assert sent[0][1] == 1
    assert "高津スポーツセンター" in sent[0][2]
    assert not pending_file.exists()


def test_write_scrape_log_and_summary(tmp_path, monkeypatch):
    import json
    from datetime import datetime

    log_path = tmp_path / "scrape-log.json"
    md_path = tmp_path / ".scrape_summary.md"
    monkeypatch.setattr(st, "SCRAPE_LOG_PATH", log_path)
    monkeypatch.setattr(st, "SCRAPE_SUMMARY_MD_PATH", md_path)
    monkeypatch.setattr(
        st,
        "_fetch_previous_scrape_log",
        lambda: {"updatedAt": "2026-10-24T00:00:00Z", "groups": {"tennis": {"group": "tennis", "status": "ok"}}},
    )

    t0 = datetime(2026, 10, 24, 17, 0, 0, tzinfo=st.JST)
    t1 = datetime(2026, 10, 24, 17, 5, 30, tzinfo=st.JST)
    st.write_scrape_log_and_summary(
        group="hall",
        started_at=t0,
        finished_at=t1,
        status="ok",
        maintenance=False,
        facility_diags=[
            {
                "id": "takatsu",
                "name": "高津スポーツセンター",
                "status": "ok",
                "days": 30,
                "reservableDays": 3,
                "okAreas": ["大体育室半面Ａ"],
                "failedAreas": [],
                "unknownRatio": 0.0,
                "unknownSamples": [],
                "notifyAdded": 1,
                "notifyRemoved": 0,
            }
        ],
        flow_log=[{"time": "2026-10-24T17:00:02+09:00", "msg": "ログイン成功"}],
        warnings=[],
        line_quota={"usage": 61, "limit": 200, "type": "limited"},
    )

    assert log_path.exists()
    saved = json.loads(log_path.read_text(encoding="utf-8"))
    # 既存の tennis グループが保持され、hall グループが追加されていること
    assert "tennis" in saved["groups"]
    assert saved["groups"]["hall"]["durationSec"] == 330.0
    assert saved["groups"]["hall"]["lineQuota"]["usage"] == 61
    assert md_path.exists()
    assert "高津スポーツセンター" in md_path.read_text(encoding="utf-8")


# ── エリア名正規化・境界一致テスト ──────────────
def test_match_area_link_text_handles_parentheses_and_fullwidth():
    from backend.scrapers.fureai_net import match_area_link_text

    # カルッツかわさき：括弧付き実画面表記と括弧なし設定の照合（全面とは誤一致しない）
    assert match_area_link_text("カルッツかわさき大体育室（全面）", "大体育室半面Ａ") is False
    assert match_area_link_text("カルッツかわさき大体育室（半面Ａ）", "大体育室半面Ａ") is True
    assert match_area_link_text("カルッツかわさき大体育室（半面Ｂ）", "大体育室半面Ｂ") is True
    assert match_area_link_text("カルッツかわさき小体育室", "小体育室") is True

    # テニスコート：全角数字の実画面表記と半角数字設定の照合（1 が 10〜12 に誤一致しない）
    assert match_area_link_text("等々力テニスコート等々力テニス場１", "等々力テニス場1") is True
    assert match_area_link_text("等々力テニスコート等々力テニス場１０", "等々力テニス場1") is False
    assert match_area_link_text("等々力テニスコート等々力テニス場１０", "等々力テニス場10") is True
    assert match_area_link_text("富士見テニスコート富士見テニス場１２", "富士見テニス場1") is False
    assert match_area_link_text("富士見テニスコート富士見テニス場１２", "富士見テニス場12") is True
    assert match_area_link_text("大師テニスコート大師テニス場３", "大師テニス場3") is True

    # とどろきアリーナ：サブアリーナが「サブアリーナ（全面）」に一致し「半面Ａ」には一致しない
    assert match_area_link_text("とどろきアリーナサブアリーナ（全面）", "サブアリーナ") is True
    assert match_area_link_text("とどろきアリーナサブアリーナ半面Ａ", "サブアリーナ") is False


def test_overlay_previous_missing_periods_and_extract_error_logs():
    cfg = _cfg()
    payload = st._build_payload(
        cfg,
        [
            _rec("2026-07-04", "大体育室半面Ａ", "partial", "一部空き", {}),
            _rec("2026-07-05", "大体育室半面Ａ", "occupied", "予約あり", {}),
        ],
    )
    previous = st._build_payload(
        cfg,
        [
            _rec("2026-07-04", "大体育室半面Ａ", "partial", "一部空き", {"午前": "available"}),
            _rec("2026-07-05", "大体育室半面Ａ", "available", "全て空き", {"夜間": "available"}),
        ],
    )
    st._overlay_previous_missing_periods(payload, previous, ["大体育室半面Ａ"])
    # 7/04 は日単位ステータスが同じ partial なので前回 periods が補完される
    assert payload["days"][0]["byArea"]["大体育室半面Ａ"]["periods"] == {"午前": "available"}
    # 7/05 は日単位ステータスが変化（available -> occupied）しているため補完しない
    assert payload["days"][1]["byArea"]["大体育室半面Ａ"]["periods"] == {}

    # エラーログ抽出：後続の info ログが大量にあっても warn/error が保持される
    flow = (
        [{"time": "2026-10-25T13:30:00+09:00", "level": "error", "msg": "月間カレンダー遷移エラー"}]
        + [{"time": f"2026-10-25T13:31:{i:02d}+09:00", "level": "info", "msg": "通常ログ"} for i in range(50)]
    )
    errs = st._extract_error_logs(flow)
    assert len(errs) == 1
    assert "月間カレンダー遷移エラー" in errs[0]


def test_is_night_maintenance_window():
    from datetime import datetime
    jst = st.JST
    # 深夜時間帯 (0:00〜06:29) は True
    assert st.is_night_maintenance_window(datetime(2026, 10, 29, 0, 0, tzinfo=jst)) is True
    assert st.is_night_maintenance_window(datetime(2026, 10, 29, 1, 34, tzinfo=jst)) is True
    assert st.is_night_maintenance_window(datetime(2026, 10, 29, 6, 29, tzinfo=jst)) is True
    # 稼働時間帯 (06:30〜23:59) は False
    assert st.is_night_maintenance_window(datetime(2026, 10, 29, 6, 30, tzinfo=jst)) is False
    assert st.is_night_maintenance_window(datetime(2026, 10, 29, 7, 7, tzinfo=jst)) is False
    assert st.is_night_maintenance_window(datetime(2026, 10, 29, 21, 0, tzinfo=jst)) is False
    assert st.is_night_maintenance_window(datetime(2026, 10, 29, 23, 59, tzinfo=jst)) is False

