"""
ふれあいネット（川崎市公共施設予約システム）スクレイパー
URL: https://www.fureai-net.city.kawasaki.jp/web/
Java Struts ベースのシステム（.do エンドポイント）
"""
import asyncio
import re
from datetime import datetime, timedelta
from backend.scrapers.base import BaseScraper

BASE_URL = "https://www.fureai-net.city.kawasaki.jp"
TOP_URL = f"{BASE_URL}/web/"

# 施設ナビゲーション設定
# region: 地域選択画面のリンクテキスト
# building: 館選択画面のリンクテキスト
# has_lottery: 抽選申込みが存在するか
# areas: 施設選択画面のリスト（index は施設一覧ページ内の出現順）
#        空リストの場合は実行時に自動発見する
FACILITY_NAV = {
    "takatsu_sports_center": {
        "region": "高津区",
        "building": "高津スポーツセンター",
        "has_lottery": True,
        "areas": [
            {"name": "大体育室半面Ａ", "index": 0},
            {"name": "大体育室半面Ｂ", "index": 1},
            {"name": "小体育室",       "index": 2},
        ],
    },
    "todoroki_tennis": {
        "region": "中原区",
        "building": "等々力テニスコート",
        "has_lottery": True,
        "areas": [],  # 実行時に自動発見
    },
    "tama_sports_center": {
        "region": "多摩区",
        "building": "川崎市多摩スポーツセンター",
        "has_lottery": True,
        "areas": [
            {"name": "テニス場Ａ",          "index": 0},
            {"name": "テニス場Ｂ",          "index": 1},
            {"name": "大体育室半面Ａ",      "index": 2},
            {"name": "大体育室半面Ｂ",      "index": 3},
            {"name": "小体育室",            "index": 4},
            {"name": "第１武道室",          "index": 5},
            {"name": "第２武道室",          "index": 6},
            {"name": "第１・２武道室合併",  "index": 7},
            {"name": "研修室",              "index": 8},
        ],
    },
}

# 全角数字→半角（午後１→午後1）
_PERIOD_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")


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

    async def login(self) -> bool:
        """ログイン。成功したら True を返す。"""
        if not (self.user_id and self.password):
            self._log("ログイン情報未設定（user_id / password が必要）")
            return False

        await self.page.goto(TOP_URL, wait_until="networkidle")
        try:
            await self.page.fill("input#userId", self.user_id)
            await self.page.fill("input#pass", self.password)
            if self.security_no:
                await self.page.fill("input#securityNo", self.security_no)
            await self.page.click("input[type='submit'][value='ログイン']")
            await self.page.wait_for_load_state("networkidle")

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
                    self._log("ログイン不可: ふれあいネットのメンテナンス時間（ログイン機能停止）")
                    self.maintenance = True
                    success = False

            self.is_logged_in = success
            if self.is_logged_in:
                self._log(f"ログイン成功: {self.page.url}")
                # 「伝言表示画面」（メッセージ確認ページ）が出たら「次へ」で突破
                await self._dismiss_message_if_present()
            else:
                self._log(f"ログイン失敗: {self.page.url}")
            return self.is_logged_in
        except Exception as e:
            self._log(f"ログイン例外: {e}")
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
        raise RuntimeError(f"メニューリンクが見つかりません: {candidates}")

    async def _click_button(self, value: str) -> None:
        """ボタン（value属性）をクリックして遷移を待つ。テキストはJS引数で渡しencoding問題を回避。"""
        async with self.page.expect_navigation(wait_until="networkidle"):
            await self.page.evaluate(
                "val => { const b = [...document.querySelectorAll('input[type=submit],input[type=button]')]"
                ".find(b => b.value === val); if(b) b.click(); else throw new Error('Button not found: '+val); }",
                value,
            )

    async def _click_link_text(self, text: str) -> None:
        """テキストリンクをクリックして遷移を待つ。テキストはJS引数で渡しencoding問題を回避。"""
        async with self.page.expect_navigation(wait_until="networkidle"):
            await self.page.evaluate(
                "t => { const a = [...document.querySelectorAll('a')]"
                ".find(a => a.textContent.includes(t)); if(a) a.click();"
                " else throw new Error('Link not found: '+t); }",
                text,
            )

    async def _click_area_link(self, area_name: str) -> None:
        """施設エリアリンク（sendInstNo）をクリックして月間カレンダーへ遷移"""
        async with self.page.expect_navigation(wait_until="networkidle"):
            await self.page.evaluate(
                "t => { const a = [...document.querySelectorAll('a')]"
                ".find(a => a.href.includes('sendInstNo') && a.textContent.includes(t));"
                " if(a) a.click(); else throw new Error('Area link not found: '+t); }",
                area_name,
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
            self._log(f"施設ナビ設定なし: {facility_id}")
            return False
        area_name = area["name"]
        area_pos = area["index"]
        try:
            await self._click_menu_link(booking_type)
            await self._click_button("地域から")
            await self._click_link_text(nav["region"])
            await self._click_link_text(nav["building"])
            # 名前で一致するリンクを探し、なければ位置で代替
            found = await self.page.evaluate(
                "t => !!([...document.querySelectorAll('a')].find("
                "a => a.href.includes('sendInstNo') && a.textContent.includes(t)))",
                area_name,
            )
            if found:
                await self._click_area_link(area_name)
            else:
                self._log(f"名前 '{area_name}' が見つからないため位置 {area_pos} で代替")
                async with self.page.expect_navigation(wait_until="networkidle"):
                    await self.page.evaluate(
                        "idx => { const ls = [...document.querySelectorAll('a')]"
                        ".filter(a => a.href.includes('sendInstNo'));"
                        " if(ls[idx]) ls[idx].click();"
                        " else throw new Error('Area index out of range: '+idx); }",
                        area_pos,
                    )
            return True
        except Exception as e:
            self._log(f"月間カレンダー遷移エラー ({facility_id}/{area_name}/{booking_type}): {e}")
            return False

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
            self._log(f"日リンククリック失敗 ({month}/{day}): {e}")
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

    async def check_area_days(
        self,
        facility_id: str,
        area_name: str,
        area_index: int,
        booking_type: str,
        target_dates: list[str],
        on_progress=None,
    ) -> list[dict]:
        """指定エリアの各日について、日レベル状態＋（空き日のみ）時間帯別状態を返す。
        戻り値: [{facility_id, area_name, booking_type, date, status, status_raw, periods}]
        periods = {'午前':'occupied', '午後1':'available', ...}（取得できた日のみ）"""
        if not self.is_logged_in:
            if not await self.login():
                return []

        area = {"name": area_name, "index": area_index}
        if on_progress:
            on_progress(f"{area_name} チェック開始")

        if not await self._navigate_to_monthly_calendar(facility_id, area, booking_type):
            return []

        target_months = sorted({d[:7] for d in target_dates})
        results: dict = {}
        period_cache: dict = {}

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
                }

            # 空き（全て空き/一部空き）の日をドリルし、週単位で時間帯を取得
            for date_str in month_dates:
                if date_str in period_cache:
                    continue
                day = str(int(date_str.split("-")[2]))
                dd = cur["days"].get(day)
                if dd and dd["clickable"] and dd["status"] in ("available", "partial"):
                    if await self._click_day(month, int(day)):
                        week = await self._extract_day_periods(year, month)
                        period_cache.update(week)
                        if on_progress:
                            on_progress(f"{area_name} {date_str} 時間帯取得")
                        # 月間カレンダーへ戻る
                        await self._navigate_to_monthly_calendar(facility_id, area, booking_type)
                        await self._move_to_month(year, month)
                        cur = await self._extract_monthly_calendar()

            # キャッシュから時間帯を割り当て（同じ週の予約あり/開放日にも付く）
            for date_str in month_dates:
                if date_str in period_cache:
                    results[date_str]["periods"] = period_cache[date_str]

        return list(results.values())

    def _log(self, msg: str):
        self._flow_log.append({"time": datetime.utcnow().isoformat(), "msg": msg})
        print(f"[FureaiNet] {msg}")


# ──────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────

def get_target_dates(days_ahead: int = 60) -> list[str]:
    """今日から n 日分の日付リストを返す"""
    today = datetime.today()
    return [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_ahead)]
