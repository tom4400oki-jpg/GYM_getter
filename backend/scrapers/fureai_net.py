"""
ふれあいネット（川崎市公共施設予約システム）スクレイパー
URL: https://www.fureai-net.city.kawasaki.jp/web/
Java Struts ベースのシステム（.do エンドポイント）
"""
import asyncio
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from backend.scrapers.base import BaseScraper

JST = timezone(timedelta(hours=9))

BASE_URL = "https://www.fureai-net.city.kawasaki.jp"
TOP_URL = f"{BASE_URL}/web/"

# 施設ナビゲーション設定
# region: 地域選択画面のリンクテキスト
# building: 館選択画面のリンクテキスト
# has_lottery: 抽選申込みが存在するか
# areas: 施設選択画面のリスト（index は「すべて」を除いた sendInstNo リンクの出現順）
FACILITY_NAV = {
    "takatsu_sports_center": {
        "region": "高津区",
        "building": "高津スポーツセンター",
        "has_lottery": True,
        # sendInstNo 順: 0=大体育室(全面), 1=半面Ａ, 2=半面Ｂ, 3=小体育室
        "areas": [
            {"name": "大体育室半面Ａ", "index": 1},
            {"name": "大体育室半面Ｂ", "index": 2},
            {"name": "小体育室",       "index": 3},
        ],
    },
    "culttz_kawasaki": {
        "region": "川崎区",
        "building": "カルッツかわさき",
        "has_lottery": True,
        # 実画面表記は「カルッツかわさき大体育室（半面Ａ）」等（括弧付き）。
        # sendInstNo 順: 0=大体育室（全面）, 1=大体育室（半面Ａ）, 2=大体育室（半面Ｂ）, 3=小体育室
        "areas": [
            {"name": "大体育室半面Ａ", "index": 1},
            {"name": "大体育室半面Ｂ", "index": 2},
            {"name": "小体育室",       "index": 3},
        ],
    },
    "asao_sports_center": {
        "region": "麻生区",
        "building": "麻生スポーツセンター",
        "has_lottery": True,
        # sendInstNo 順: 0=大体育室(全面), 1=半面Ａ, 2=半面Ｂ
        "areas": [
            {"name": "大体育室半面Ａ", "index": 1},
            {"name": "大体育室半面Ｂ", "index": 2},
        ],
    },
    "saiwai_sports_center": {
        "region": "幸区",
        "building": "幸スポーツセンター",
        "has_lottery": True,
        # sendInstNo 順: 0=大体育室(全面), 1=半面Ａ, 2=半面Ｂ, 3=小体育室
        "areas": [
            {"name": "大体育室半面Ａ", "index": 1},
            {"name": "大体育室半面Ｂ", "index": 2},
            {"name": "小体育室",       "index": 3},
        ],
    },
    "miyamae_sports_center": {
        "region": "宮前区",
        "building": "宮前スポーツセンター",
        "has_lottery": True,
        # sendInstNo 順: 0=大体育室(全面), 1=半面Ａ, 2=半面Ｂ
        "areas": [
            {"name": "大体育室半面Ａ", "index": 1},
            {"name": "大体育室半面Ｂ", "index": 2},
        ],
    },
    "kawasaki_civic_plaza": {
        "region": "高津区",
        "building": "川崎市民プラザ",
        "has_lottery": True,
        # sendInstNo 順: 24=川崎市民プラザ体育館
        "areas": [
            {"name": "体育館", "index": 24},
        ],
    },
    "todoroki_arena": {
        "region": "中原区",
        "building": "とどろきアリーナ",
        "has_lottery": True,
        # sendInstNo 順: 0=メインアリーナ, 1=サブアリーナ（全面）
        "areas": [
            {"name": "メインアリーナ", "index": 0},
            {"name": "サブアリーナ",   "index": 1},
        ],
    },
    "todoroki_tennis": {
        "region": "中原区",
        "building": "等々力テニスコート",
        "has_lottery": True,
        # 実画面表記は全角数字「等々力テニスコート等々力テニス場１〜１０」（sendInstNo 0〜9）
        "areas": [
            {"name": f"等々力テニス場{i}", "index": i - 1} for i in range(1, 11)
        ],
    },
    "tama_tennis": {
        "region": "多摩区",
        "building": "川崎市多摩スポーツセンター",
        "has_lottery": True,
        # sendInstNo 順: 0=アーチェリー, 1=野球場, 2=テニス場Ａ, 3=テニス場Ｂ
        "areas": [
            {"name": "テニス場Ａ", "index": 2},
            {"name": "テニス場Ｂ", "index": 3},
        ],
    },
    "daishi_tennis": {
        "region": "川崎区",
        "building": "大師テニスコート",
        "has_lottery": True,
        # 実画面表記は全角数字「大師テニスコート大師テニス場１〜３」（sendInstNo 0〜2）
        "areas": [
            {"name": f"大師テニス場{i}", "index": i - 1} for i in range(1, 4)
        ],
    },
    "fujimi_tennis": {
        "region": "川崎区",
        "building": "富士見テニスコート",
        "has_lottery": True,
        # 実画面表記は全角数字「富士見テニスコート富士見テニス場１〜１２」（sendInstNo 0〜11）
        "areas": [
            {"name": f"富士見テニス場{i}", "index": i - 1} for i in range(1, 13)
        ],
    },
    "tama_sports_center": {
        "region": "多摩区",
        "building": "川崎市多摩スポーツセンター",
        "has_lottery": True,
        # sendInstNo 順: 0=アーチェリー, 1=野球場, 2=テニスＡ, 3=テニスＢ, 4=大体育室(全面), 5=半面Ａ, 6=半面Ｂ
        "areas": [
            {"name": "大体育室半面Ａ", "index": 5},
            {"name": "大体育室半面Ｂ", "index": 6},
        ],
    },
}

# 全角数字→半角（午後１→午後1）
_PERIOD_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_area_text(text: str) -> str:
    """エリア名比較用の正規化：NFKC（全角英数→半角）＋空白と括弧の除去。
    例: 'カルッツかわさき大体育室（半面Ａ）' -> 'カルッツかわさき大体育室半面A'
        '等々力テニスコート等々力テニス場１０' -> '等々力テニスコート等々力テニス場10'"""
    s = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[\s()（）]+", "", s)


def match_area_link_text(link_text: str, target_name: str) -> bool:
    """施設選択画面のリンク文字列が target_name に対応するかを判定する。
    - 全角/半角や括弧の有無（例: '大体育室（半面Ａ）' vs '大体育室半面Ａ'）を吸収する。
    - 'テニス場1' が 'テニス場10' に部分一致してしまうのを防ぐため、末尾が数字の場合は
      直後に数字が続かないことを要求する。
    - 'サブアリーナ' が 'サブアリーナ半面Ａ' より先に 'サブアリーナ（全面）' に一致するよう、
      完全末尾一致または '全面' 付きの末尾一致を優先許容する。"""
    norm_link = normalize_area_text(link_text)
    norm_target = normalize_area_text(target_name)
    if not norm_link or not norm_target:
        return False
    pattern = re.escape(norm_target) + (r"(?!\d)" if norm_target[-1].isdigit() else r"(?:全面)?$")
    return re.search(pattern, norm_link) is not None

# テニス（時間貸し）の開始時刻4桁 → アプリのスロットラベル
_TENNIS_SLOT_MAP = {
    "0900": "9:00", "1100": "11:00", "1200": "12:00", "1300": "13:00",
    "1400": "14:00", "1500": "15:00", "1600": "16:00", "1700": "17:00",
    "1830": "18:30",
}


def map_status(alt: str) -> str:
    """img の alt 文字列からステータスを判定（部分一致）。
    月間カレンダーの空き日は '2026年6月8日一部空き' のように日付が前置されるため
    完全一致ではなく部分一致で判定する。順序が重要（一部空き/全て空きを先に）。"""
    a = alt or ""
    if "受付期間外" in a:
        return "out_of_period"
    if "一部空き" in a:
        return "partial"
    if "全て空き" in a:
        return "available"
    if "予約あり" in a:
        return "occupied"
    if "一般開放" in a:
        return "open"
    if "保守" in a or "主催事業" in a:
        return "closed"
    if "休館" in a:
        return "closed"
    if "雨" in a:
        return "rain"
    if "取消" in a:
        return "cancelling"
    if "開放予定" in a:
        return "planned"
    if "空き" in a:  # 時間帯ページの「空き」（一部/全ての判定後）
        return "available"
    return "unknown"


class FureaiNetScraper(BaseScraper):
    """ふれあいネット専用スクレイパー"""

    def __init__(self, headless: bool = True, user_id: str = "", password: str = "", security_no: str = ""):
        super().__init__(headless=headless)
        self.user_id = user_id
        self.password = password
        self.security_no = security_no
        self.is_logged_in = False
        self.maintenance = False  # ログイン機能停止（メンテ時間）を検出したら True
        self._flow_log: list[dict] = []

    # ──────────────────────────────────────────
    # ログイン
    # ──────────────────────────────────────────

    async def login(self, max_attempts: int = 2) -> bool:
        """ログイン。成功したら True を返す。一時的な通信エラー時はリトライする。"""
        self.is_logged_in = False
        if not (self.user_id and self.password):
            self._log("ログイン情報未設定（user_id / password が必要）", level="error")
            return False

        for attempt in range(1, max_attempts + 1):
            try:
                # 再ログイン時に壊れた Struts セッション (JSESSIONID) が残っていると
                # ログインフォームが表示されないことがあるため Cookie をクリアする
                try:
                    await self.page.context.clear_cookies()
                except Exception:
                    pass

                await self.page.goto(TOP_URL, wait_until="networkidle")
                await self.page.fill("input#userId", self.user_id)
                await self.page.fill("input#pass", self.password)
                if self.security_no:
                    await self.page.fill("input#securityNo", self.security_no)
                async with self.page.expect_navigation(wait_until="networkidle"):
                    await self.page.click("input[type='submit'][value='ログイン']")

                # ログイン成功判定：URLが変わっていれば成功
                success = self.page.url != TOP_URL
                if not success:
                    # ログアウトボタンがあれば成功
                    success = await self.page.locator("input[value='ログアウト']").count() > 0

                # メンテ時間（ログイン機能停止）/エラー画面の検出
                if success:
                    try:
                        page_title = await self.page.title()
                        body_txt = await self.page.evaluate(
                            "() => document.body ? document.body.innerText : ''"
                        )
                    except Exception:
                        page_title, body_txt = "", ""
                    if "ログイン機能停止" in body_txt or "エラー画面" in page_title:
                        self._log("ログイン不可: ふれあいネットのメンテナンス時間（ログイン機能停止）", level="warn")
                        self.maintenance = True
                        self.is_logged_in = False
                        return False

                self.is_logged_in = success
                if self.is_logged_in:
                    self._log(f"ログイン成功: {self.page.url}")
                    # 「伝言表示画面」（メッセージ確認ページ）が出たら「次へ」で突破
                    await self._dismiss_message_if_present()
                    return True

                self._log(f"ログイン失敗 (試行 {attempt}/{max_attempts}): {self.page.url}", level="warn")
            except Exception as e:
                self.is_logged_in = False
                self._log(f"ログイン例外 (試行 {attempt}/{max_attempts}): {e}", level="warn")

            if attempt < max_attempts:
                await asyncio.sleep(2)

        return False

    async def _dismiss_message_if_present(self) -> None:
        """「伝言表示画面」など確認ページの「次へ」ボタンを押してメニューへ進む"""
        for _ in range(3):
            next_btn = await self.page.evaluate(
                "() => !!document.querySelector('input[type=submit][value=\"次へ\"],"
                " input[type=button][value=\"次へ\"]')"
            )
            if not next_btn:
                break
            self._log(f"確認ページを突破: {self.page.url}")
            async with self.page.expect_navigation(wait_until="networkidle"):
                await self.page.evaluate(
                    "() => { const b = document.querySelector('input[value=\"次へ\"]'); if(b) b.click(); }"
                )

    # ──────────────────────────────────────────
    # ナビゲーションヘルパー
    # ──────────────────────────────────────────

    async def _click_menu_link(self, booking_type: str = "reservation") -> None:
        """
        どのページからでも予約/抽選メニューへ戻る。
        booking_type: "reservation" → 予約の申込み, "lottery" → 抽選の申込み
        ページに応じてリンクテキストが変わる場合に対応。
        """
        if booking_type == "lottery":
            candidates = ["抽選の申込み", "抽選"]
        else:
            candidates = ["予約の申込み", "予約"]
        for text in candidates:
            found = await self.page.evaluate(
                "t => !!([...document.querySelectorAll('a')].find(a => a.textContent.includes(t)))",
                text,
            )
            if found:
                await self._click_link_text(text)
                return
        cur_links = await self.page.evaluate(
            "() => [...document.querySelectorAll('a')].map(a => a.textContent.trim()).filter(Boolean).slice(0, 12)"
        )
        raise RuntimeError(f"メニューリンクが見つかりません: {candidates} (url={self.page.url}, links={cur_links})")

    async def _click_button(self, value: str) -> None:
        """ボタン（value属性）をクリックして遷移を待つ。テキストはJS引数で渡しencoding問題を回避。"""
        async with self.page.expect_navigation(wait_until="networkidle"):
            await self.page.evaluate(
                "val => { const bs = [...document.querySelectorAll('input[type=submit],input[type=button]')];"
                " const b = bs.find(b => b.value === val);"
                " if(b) b.click();"
                " else throw new Error(`Button not found: ${val} (url=${location.pathname}, btns=${bs.map(x=>x.value).join(',')})`); }",
                value,
            )

    async def _click_link_text(self, text: str) -> None:
        """テキストリンクをクリックして遷移を待つ。テキストはJS引数で渡しencoding問題を回避。"""
        async with self.page.expect_navigation(wait_until="networkidle"):
            await self.page.evaluate(
                "t => { const as = [...document.querySelectorAll('a')];"
                " const a = as.find(a => a.textContent.includes(t));"
                " if(a) a.click();"
                " else throw new Error(`Link not found: ${t} (url=${location.pathname}, links=${as.map(x=>x.textContent.trim()).filter(Boolean).slice(0,12).join(',')})`); }",
                text,
            )

    async def _move_to_month(self, year: int, month: int) -> None:
        """月間カレンダーで指定月に移動（次の月/前の月ボタンをクリック）"""
        for _ in range(14):
            cal = await self._extract_monthly_calendar()
            if not cal["year_month"]:
                break
            c_y, c_m = map(int, cal["year_month"].split("-"))
            if (c_y, c_m) == (year, month):
                break
            btn = "次の月" if (year, month) > (c_y, c_m) else "前の月"
            await self._click_button(btn)

    async def _navigate_to_monthly_calendar(
        self, facility_id: str, area: dict, booking_type: str = "reservation"
    ) -> bool:
        """
        指定施設エリアの月間カレンダー画面まで遷移する。
        ログイン済みであること。
        area: {"name": str, "index": int}
        """
        nav = FACILITY_NAV.get(facility_id)
        if not nav:
            self._log(f"施設ナビ設定なし: {facility_id}", level="error")
            return False
        area_name = area["name"]
        area_pos = area["index"]
        try:
            await self._click_menu_link(booking_type)
            await self._click_button("地域から")
            await self._click_link_text(nav["region"])
            await self._click_link_text(nav["building"])

            # 「すべて」を除いた施設エリアリンク（sendInstNo）を取得し、NFKC正規化＋境界一致で照合
            inst_links: list[str] = await self.page.evaluate(
                "() => [...document.querySelectorAll('a')]"
                ".filter(a => (a.getAttribute('href') || '').includes('sendInstNo') && !a.textContent.includes('すべて'))"
                ".map(a => a.textContent.trim().replace(/\\s+/g, ' '))"
            )
            matched_idx = next(
                (i for i, txt in enumerate(inst_links) if match_area_link_text(txt, area_name)),
                None,
            )
            if matched_idx is not None:
                target_idx = matched_idx
            elif 0 <= area_pos < len(inst_links):
                target_idx = area_pos
                self._log(
                    f"名前 '{area_name}' が一覧 {inst_links[:6]} に一致しないため位置 {area_pos} ('{inst_links[area_pos]}') で代替",
                    level="warn",
                )
            else:
                raise RuntimeError(
                    f"Area index out of range: {area_pos} (url={self.page.url}, inst_links={inst_links[:8]})"
                )

            async with self.page.expect_navigation(wait_until="networkidle"):
                await self.page.evaluate(
                    "idx => { const ls = [...document.querySelectorAll('a')]"
                    ".filter(a => (a.getAttribute('href') || '').includes('sendInstNo') && !a.textContent.includes('すべて'));"
                    " if(ls[idx]) ls[idx].click();"
                    " else throw new Error('Area index out of range: '+idx); }",
                    target_idx,
                )
            return True
        except Exception as e:
            self._log(f"月間カレンダー遷移エラー ({facility_id}/{area_name}/{booking_type}): {e}", level="error")
            return False

    async def _return_to_monthly_calendar(
        self, facility_id: str, area: dict, booking_type: str, year: int, month: int
    ) -> dict | None:
        """時間帯詳細ページから指定年月の月間カレンダーへ復帰し、抽出したカレンダー辞書を返す。
        1) まず画面上の「もどる」ボタン（1クリックで同エリア・同年月の月間カレンダーへ直帰）を試す。
        2) 失敗した場合のみ、メニューからの再遷移 → 再ログイン自己修復へフォールバックする。"""
        expected_ym = f"{year}-{month:02d}"
        try:
            has_back = await self.page.evaluate(
                "() => [...document.querySelectorAll('input[type=submit],input[type=button]')]"
                ".some(b => b.value === 'もどる')"
            )
            if has_back:
                await self._click_button("もどる")
                cal = await self._extract_monthly_calendar()
                if cal.get("days"):
                    if cal.get("year_month") != expected_ym:
                        await self._move_to_month(year, month)
                        cal = await self._extract_monthly_calendar()
                    if cal.get("days"):
                        return cal
        except Exception as e:
            self._log(
                f"「もどる」ボタン復帰失敗 ({facility_id}/{area['name']}): {e} → メニュー再遷移へフォールバック",
                level="warn",
            )

        if not await self._navigate_to_monthly_calendar(facility_id, area, booking_type):
            if not await self._recover_and_navigate(facility_id, area, booking_type):
                return None
        try:
            await self._move_to_month(year, month)
            cal = await self._extract_monthly_calendar()
            if cal.get("days"):
                return cal
        except Exception as e:
            self._log(
                f"フォールバック月移動/抽出エラー ({facility_id}/{area['name']}/{expected_ym}): {e}",
                level="error",
            )
        return None

    # ──────────────────────────────────────────
    # 月間カレンダー解析
    # ──────────────────────────────────────────

    async def _extract_monthly_calendar(self) -> dict:
        """
        現在の月間カレンダーページから空き状況を抽出。
        戻り値: {
            "year_month": "2026-04",
            "facility_name": "高津スポーツセンター 大体育室半面Ａ",
            "days": { "1": {"status_raw": "予約あり", "status": "occupied", "clickable": False}, ... }
        }
        """
        data = await self.page.evaluate("""
            () => {
                const result = { year_month: '', facility_name: '', days: {} };

                // 施設名
                const caption = document.querySelector('table caption');
                result.facility_name = caption ? caption.innerText.trim() : '';

                // 年月
                const head = document.querySelector('td.m_akitablelist_head');
                if (head) {
                    const m = head.innerText.match(/(\\d{4})年(\\d{1,2})月/);
                    if (m) result.year_month = `${m[1]}-${m[2].padStart(2, '0')}`;
                }

                // 各日
                document.querySelectorAll('td.m_akitablelist').forEach(td => {
                    const span = td.querySelector('span');
                    const img = td.querySelector('img');
                    const link = td.querySelector('a');
                    if (!span || !img) return;
                    const day = span.innerText.trim().replace('日', '');
                    result.days[day] = {
                        status_raw: img.alt,
                        clickable: !!link,
                        href: link ? link.href : null
                    };
                });

                return result;
            }
        """)
        # status_raw → status（部分一致で判定）
        for d in data["days"].values():
            d["status"] = map_status(d["status_raw"])
        return data

    # ──────────────────────────────────────────
    # 時間帯（午前/午後1/午後2/夜間）ベースの空き確認
    # ──────────────────────────────────────────

    async def _click_day(self, month: int, day: int) -> bool:
        """月間カレンダーで指定日の selectDay リンクをクリックし時間帯ページへ遷移"""
        try:
            async with self.page.expect_navigation(wait_until="networkidle"):
                await self.page.evaluate(
                    """args => {
                        const [m, d] = args;
                        const re = new RegExp(',\\\\s*' + m + ',\\\\s*' + d + '\\\\)');
                        const a = [...document.querySelectorAll('a')]
                            .find(a => /selectDay/.test(a.href) && re.test(a.href));
                        if (a) a.click(); else throw new Error('day link not found ' + m + '/' + d);
                    }""",
                    [month, day],
                )
            return True
        except Exception as e:
            self._log(f"日リンククリック失敗 ({month}/{day}): {e}", level="warn")
            return False

    async def _extract_day_periods(self, base_year: int, base_month: int) -> dict:
        """時間帯貸しページ（週表示）から { 'YYYY-MM-DD': {period: status} } を抽出する。
        行 = 午前/午後1/午後2/夜間、列 = 7日。img alt 例: '6月8日月曜日午後２空き'"""
        raw = await self.page.evaluate(r"""() => {
            const tables = [...document.querySelectorAll('table')];
            for (const t of tables) {
                const rows = [...t.querySelectorAll('tr')].map(tr =>
                    [...tr.querySelectorAll('th,td')].map(c => {
                        const img = c.querySelector('img');
                        return { text: c.innerText.trim().replace(/\s+/g, ''), alt: img ? img.alt : '' };
                    })
                );
                if (rows.some(r => r[0] && /午前|午後|夜間/.test(r[0].text))) return rows;
            }
            return null;
        }""")
        out: dict = {}
        if not raw:
            return out
        for r in raw:
            if not r:
                continue
            label = r[0]["text"]
            if not re.search(r"午前|午後|夜間", label):
                continue
            period = label.translate(_PERIOD_TRANS)
            for cell in r[1:]:
                alt = cell.get("alt", "")
                if not alt:
                    continue
                m = re.search(r"(\d+)月(\d+)日", alt)
                if not m:
                    continue
                mo, da = int(m.group(1)), int(m.group(2))
                yr = base_year + 1 if mo < base_month else base_year
                date_str = f"{yr}-{mo:02d}-{da:02d}"
                out.setdefault(date_str, {})[period] = map_status(alt)
        return out

    async def _extract_tennis_slots(self, base_year: int, base_month: int) -> dict:
        """テニス（時間貸し）の施設空き状況ページから { 'YYYY-MM-DD': {slot: status} } を抽出する。
        体育館の週表と違い、img alt に日付・時刻・状態がすべて入る。
        alt 例: '6月25日木曜日０９００予約あり'（時刻=全角4桁）。週単位で複数日分を返す。"""
        alts = await self.page.evaluate(
            "() => [...document.querySelectorAll('img')].map(i => i.alt).filter(Boolean)"
        )
        out: dict = {}
        for alt in alts:
            a = alt.translate(_PERIOD_TRANS)  # 全角数字→半角
            m = re.match(r"(\d+)月(\d+)日.*?(\d{4})(.+)$", a)
            if not m:
                continue
            mo, da, slot4, status_txt = (
                int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
            )
            slot = _TENNIS_SLOT_MAP.get(slot4)
            if not slot:
                continue
            yr = base_year + 1 if mo < base_month else base_year
            date_str = f"{yr}-{mo:02d}-{da:02d}"
            out.setdefault(date_str, {})[slot] = map_status(status_txt)
        return out

    async def _recover_and_navigate(
        self, facility_id: str, area: dict, booking_type: str = "reservation"
    ) -> bool:
        """月間カレンダー遷移に失敗した際、Cookie クリア＋再ログインを挟んで自己修復を試みる。"""
        self._log(f"ナビゲーション自己修復を試行 ({facility_id}/{area['name']})", level="warn")
        if not await self.login(max_attempts=2):
            return False
        return await self._navigate_to_monthly_calendar(facility_id, area, booking_type)

    async def check_area_days(
        self,
        facility_id: str,
        area_name: str,
        area_index: int,
        booking_type: str,
        target_dates: list[str],
        on_progress=None,
    ) -> list[dict]:
        """指定エリアの各日について、日レベル状態＋（クリック可能な週のみ）時間帯別状態を返す。
        戻り値: [{facility_id, area_name, booking_type, date, status, status_raw, periods, incomplete_weeks}]
        periods = {'午前':'occupied', '午後1':'available', ...}（取得できた日のみ）"""
        if not self.is_logged_in:
            if not await self.login():
                return []

        area = {"name": area_name, "index": area_index}
        if on_progress:
            on_progress(f"{area_name} チェック開始")

        if not await self._navigate_to_monthly_calendar(facility_id, area, booking_type):
            if not await self._recover_and_navigate(facility_id, area, booking_type):
                return []

        target_months = sorted({d[:7] for d in target_dates})
        results: dict = {}
        period_cache: dict = {}
        incomplete_weeks = False

        for month_str in target_months:
            year, month = map(int, month_str.split("-"))
            cur = await self._extract_monthly_calendar()
            if cur["year_month"] != month_str:
                await self._move_to_month(year, month)
                cur = await self._extract_monthly_calendar()

            month_dates = [d for d in target_dates if d.startswith(month_str)]

            # 日レベル状態を記録
            for date_str in month_dates:
                day = str(int(date_str.split("-")[2]))
                dd = cur["days"].get(day)
                results[date_str] = {
                    "facility_id": facility_id,
                    "area_name": area_name,
                    "booking_type": booking_type,
                    "date": date_str,
                    "status": dd["status"] if dd else "unknown",
                    "status_raw": dd["status_raw"] if dd else "",
                    "periods": {},
                    "incomplete_weeks": False,
                }

            # クリック可能な日を「週に1回」ドリルし、週単位で時間帯を取得する。
            # _extract_day_periods はその週まるごとを返すため、週内の最初の
            # クリック可能日を踏めば、同じ週の予約あり/開放日（×/◎）にも内訳が付く。
            for date_str in month_dates:
                if date_str in period_cache:
                    continue
                day = str(int(date_str.split("-")[2]))
                dd = cur["days"].get(day)
                if dd and dd["clickable"]:
                    if await self._click_day(month, int(day)):
                        # 体育館（午前/午後…）として抽出を試み、空ならテニス（時間スロット）として抽出
                        week = await self._extract_day_periods(year, month)
                        if not week:
                            week = await self._extract_tennis_slots(year, month)
                        period_cache.update(week)
                        if on_progress:
                            on_progress(f"{area_name} {date_str} 時間帯取得")
                        # 月間カレンダーへ1クリック（「もどる」ボタン優先）で戻る
                        cur_after = await self._return_to_monthly_calendar(
                            facility_id, area, booking_type, year, month
                        )
                        if not cur_after:
                            self._log(
                                f"月間カレンダーへの復帰に失敗 ({facility_id}/{area_name}/{month_str})。以降の週取得を中断します",
                                level="error",
                            )
                            incomplete_weeks = True
                            break
                        cur = cur_after

            # キャッシュから時間帯を割り当て（同じ週の予約あり/開放日にも付く）
            for date_str in month_dates:
                if date_str in period_cache:
                    results[date_str]["periods"] = period_cache[date_str]

        if incomplete_weeks:
            for r in results.values():
                r["incomplete_weeks"] = True

        return list(results.values())

    def _log(self, msg: str, level: str = "info"):
        self._flow_log.append({
            "time": datetime.now(JST).isoformat(timespec="seconds"),
            "level": level,
            "msg": msg,
        })
        prefix = f"[FureaiNet][{level.upper()}]" if level != "info" else "[FureaiNet]"
        print(f"{prefix} {msg}", flush=True)


# ──────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────

def get_target_dates(days_ahead: int = 60) -> list[str]:
    """今日（JST基準）から n 日分の日付リストを返す。
    GitHub Actions ランナー（UTC）で JST 7:00（UTC 22:00 前日）に実行された場合も
    日本時間の当日を起点にする。"""
    today = datetime.now(JST).date()
    return [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_ahead)]
