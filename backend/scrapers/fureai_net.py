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

# 月間カレンダーのステータス（img の alt 属性）
STATUS_MAP = {
    "全て空き":     "available",
    "一部空き":     "partial",
    "予約あり":     "occupied",
    "休館日":       "closed",
    "保守日・主催事業": "closed",
    "一般開放":     "open",
    "雨天":         "rain",
    "取消処理中":   "cancelling",
    "開放予定":     "planned",
    "受付期間外":   "out_of_period",
}


class FureaiNetScraper(BaseScraper):
    """ふれあいネット専用スクレイパー"""

    def __init__(self, headless: bool = True, user_id: str = "", password: str = "", security_no: str = ""):
        super().__init__(headless=headless)
        self.user_id = user_id
        self.password = password
        self.security_no = security_no
        self.is_logged_in = False
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

    async def _navigate_to_top(self) -> None:
        """メインメニューに戻る"""
        try:
            await self.page.goto(TOP_URL, wait_until="networkidle")
            await self._dismiss_message_if_present()
        except Exception as e:
            self._log(f"トップへの戻りエラー: {e}")

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

    async def _discover_areas(self, facility_id: str, booking_type: str = "reservation") -> list[dict]:
        """
        施設一覧ページからエリア一覧を動的に取得する。
        areas 設定が空の施設（等々力テニスコート等）用。
        """
        nav = FACILITY_NAV.get(facility_id)
        if not nav:
            return []
        try:
            await self._click_menu_link(booking_type)
            await self._click_button("地域から")
            await self._click_link_text(nav["region"])
            await self._click_link_text(nav["building"])
            links = await self.page.evaluate("""
                () => [...document.querySelectorAll('a')]
                    .filter(a => a.href.includes('sendInstNo'))
                    .map((a, i) => ({
                        name: a.innerText.trim().replace(/\\n/g, ' ').trim(),
                        index: i
                    }))
            """)
            self._log(f"発見したエリア ({facility_id}/{booking_type}): {[l['name'] for l in links]}")
            return links
        except Exception as e:
            self._log(f"エリア発見エラー ({facility_id}/{booking_type}): {e}")
            return []

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
        # status_raw → status
        for d in data["days"].values():
            d["status"] = STATUS_MAP.get(d["status_raw"], "unknown")
        return data

    # ──────────────────────────────────────────
    # 週間ビュー解析（空き日をクリックした後）
    # ──────────────────────────────────────────

    async def _extract_weekly_slots(self, facility_id: str, area_name: str) -> list[dict]:
        """
        一週間空き検索結果ページから時間帯別スロットを抽出。
        """
        slots = []
        checked_at = datetime.utcnow().isoformat()

        try:
            table_data = await self.page.evaluate("""
                () => {
                    const results = [];
                    // ヘッダー行から日付を取得
                    const header_cells = [];
                    const header_row = document.querySelector('table tr');
                    if (header_row) {
                        header_row.querySelectorAll('th').forEach(th => {
                            header_cells.push(th.innerText.trim());
                        });
                    }

                    // 各行（時間帯）を処理
                    document.querySelectorAll('table tr').forEach(row => {
                        const cells = row.querySelectorAll('td');
                        if (cells.length < 2) return;

                        const first_cell = cells[0].innerText.trim();
                        const time_match = first_cell.match(/(\\d{1,2}:\\d{2})[～~\\-〜](\\d{1,2}:\\d{2})/);
                        if (!time_match) return;

                        const time_start = time_match[1];
                        const time_end = time_match[2];

                        // 日付セル（2列目以降）
                        for (let i = 1; i < cells.length; i++) {
                            const cell = cells[i];
                            const img = cell.querySelector('img');
                            const link = cell.querySelector('a');
                            const status_text = img ? img.alt : cell.innerText.trim();
                            const date_header = header_cells[i] || '';
                            results.push({
                                date_header,
                                time_start,
                                time_end,
                                status_raw: status_text,
                                clickable: !!link,
                                booking_href: link ? link.href : null
                            });
                        }
                    });
                    return results;
                }
            """)

            for cell in table_data:
                status = STATUS_MAP.get(cell.get("status_raw", ""), "unknown")
                date_str = _parse_date_header(cell.get("date_header", ""))
                if date_str:
                    slots.append({
                        "facility_id": facility_id,
                        "area_name": area_name,
                        "date": date_str,
                        "time_start": cell["time_start"],
                        "time_end": cell["time_end"],
                        "status": status,
                        "status_raw": cell["status_raw"],
                        "booking_href": cell.get("booking_href"),
                        "checked_at": checked_at,
                    })

        except Exception as e:
            self._log(f"週間スロット抽出エラー: {e}")

        return slots

    # ──────────────────────────────────────────
    # 空き状況チェック（メイン）
    # ──────────────────────────────────────────

    async def discover_facility_areas(self, facility_id: str, on_progress=None) -> list[dict]:
        """指定施設の予約・抽選エリア一覧を取得する"""
        if not self.is_logged_in:
            if not await self.login():
                return []
        nav = FACILITY_NAV.get(facility_id)
        if not nav:
            return []
        results = []
        booking_types = ["reservation"]
        if nav.get("has_lottery"):
            booking_types.append("lottery")
        for booking_type in booking_types:
            bt_label = "予約" if booking_type == "reservation" else "抽選"
            msg = f"発見中: {facility_id} / {bt_label}"
            self._log(msg)
            if on_progress:
                on_progress(msg)
            try:
                await self._click_menu_link(booking_type)
                await self._click_button("地域から")
                await self._click_link_text(nav["region"])
                await self._click_link_text(nav["building"])
                live_links = await self.page.evaluate("""
                    () => [...document.querySelectorAll('a')]
                        .filter(a => a.href.includes('sendInstNo'))
                        .map((a, i) => ({
                            name: a.innerText.trim().replace(/\\n/g, ' ').trim(),
                            index: i
                        }))
                """)
                self._log(f"  {facility_id}/{bt_label}: {[l['name'] for l in live_links]}")
                for l in live_links:
                    results.append({
                        "facility_id": facility_id,
                        "area_name": l["name"],
                        "area_index": l["index"],
                        "booking_type": booking_type,
                    })
                # トップに戻る
                await self._navigate_to_top()
            except Exception as e:
                self._log(f"  エラー ({facility_id}/{bt_label}): {e}")
                await self._navigate_to_top()
        return results

    async def discover_all_areas(self, on_progress=None) -> list[dict]:
        """
        FACILITY_NAV の全施設について予約・抽選それぞれの施設一覧を取得する。
        戻り値: [{"facility_id", "area_name", "area_index", "booking_type"}, ...]
        """
        if not self.is_logged_in:
            if not await self.login():
                return []

        results = []
        for facility_id, nav in FACILITY_NAV.items():
            booking_types = ["reservation"]
            if nav.get("has_lottery"):
                booking_types.append("lottery")

            for booking_type in booking_types:
                bt_label = "予約" if booking_type == "reservation" else "抽選"
                msg = f"発見中: {facility_id} / {bt_label}"
                self._log(msg)
                if on_progress:
                    on_progress(msg)

                areas = nav.get("areas", [])
                if areas:
                    # 静的設定があってもサイト上の実際の名前を確認する
                    try:
                        await self._click_menu_link(booking_type)
                        await self._click_button("地域から")
                        await self._click_link_text(nav["region"])
                        await self._click_link_text(nav["building"])
                        live_links = await self.page.evaluate("""
                            () => [...document.querySelectorAll('a')]
                                .filter(a => a.href.includes('sendInstNo'))
                                .map((a, i) => ({
                                    name: a.innerText.trim().replace(/\\n/g, ' ').trim(),
                                    index: i
                                }))
                        """)
                        self._log(f"  {facility_id}/{bt_label}: {[l['name'] for l in live_links]}")
                        for l in live_links:
                            results.append({
                                "facility_id": facility_id,
                                "area_name": l["name"],
                                "area_index": l["index"],
                                "booking_type": booking_type,
                            })
                    except Exception as e:
                        self._log(f"  エラー ({facility_id}/{bt_label}): {e}")
                else:
                    discovered = await self._discover_areas(facility_id, booking_type)
                    for l in discovered:
                        results.append({
                            "facility_id": facility_id,
                            "area_name": l["name"],
                            "area_index": l["index"],
                            "booking_type": booking_type,
                        })

        return results

    async def check_single_area(
        self,
        facility_id: str,
        area_name: str,
        area_index: int,
        booking_type: str,
        target_dates: list[str],
        on_progress=None,
    ) -> list[dict]:
        """
        指定した1エリアのみの空き確認を行う。
        """
        if not self.is_logged_in:
            if not await self.login():
                return []

        area = {"name": area_name, "index": area_index}
        msg = f"チェック: {facility_id} / {area_name} / {'予約' if booking_type == 'reservation' else '抽選'}"
        self._log(msg)
        if on_progress:
            on_progress(msg)

        ok = await self._navigate_to_monthly_calendar(facility_id, area, booking_type)
        if not ok:
            return []

        nav = FACILITY_NAV.get(facility_id, {})
        target_months = sorted({d[:7] for d in target_dates})
        all_slots = []

        for month_str in target_months:
            year, month = map(int, month_str.split("-"))
            try:
                current = await self._extract_monthly_calendar()
                if current["year_month"] != month_str:
                    await self._move_to_month(year, month)
                    current = await self._extract_monthly_calendar()

                self._log(f"  {month_str}: {current['facility_name']}")

                for date_str in target_dates:
                    if not date_str.startswith(month_str):
                        continue
                    day = str(int(date_str.split("-")[2]))
                    day_data = current["days"].get(day)
                    if not day_data:
                        continue

                    status = day_data["status"]
                    all_slots.append({
                        "facility_id": facility_id,
                        "area_name": area_name,
                        "date": date_str,
                        "time_start": "",
                        "time_end": "",
                        "status": status,
                        "status_raw": day_data["status_raw"],
                        "booking_href": day_data.get("href"),
                        "booking_type": booking_type,
                        "checked_at": datetime.utcnow().isoformat(),
                    })

                    if day_data["clickable"] and status in ("available", "partial"):
                        try:
                            day_num = int(day)
                            async with self.page.expect_navigation(wait_until="networkidle"):
                                await self.page.evaluate(
                                    "d => { const a = [...document.querySelectorAll('a')]"
                                    ".find(a => { const t = a.innerText.trim();"
                                    " return t === d+'日' || t === String(d); });"
                                    " if(a) a.click(); else throw new Error('Day link: '+d); }",
                                    day_num,
                                )
                            weekly_slots = await self._extract_weekly_slots(facility_id, area_name)
                            day_slots = [s for s in weekly_slots if s["date"] == date_str]
                            for s in day_slots:
                                s["booking_type"] = booking_type
                            all_slots.extend(day_slots)
                            # 月間に戻る
                            await self._navigate_to_monthly_calendar(facility_id, area, booking_type)
                            await self._move_to_month(year, month)
                        except Exception as e:
                            self._log(f"  週間ビューエラー ({date_str}): {e}")

            except Exception as e:
                self._log(f"  月間エラー ({month_str}): {e}")

        return all_slots

    async def check_availability(
        self,
        facility: dict,
        target_dates: list[str],
        on_progress=None,  # callable(msg: str) | None — 進捗コールバック
    ) -> list[dict]:
        """
        指定施設・日付リストの空き状況を取得する。予約・抽選両方を確認。
        target_dates: ["2026-04-01", ...] 形式
        on_progress: 進捗メッセージを受け取るコールバック（DBへの中間保存など）
        """
        facility_id = facility["id"]
        nav = FACILITY_NAV.get(facility_id)
        if not nav:
            self._log(f"施設ナビ設定なし: {facility_id}")
            return []

        if not self.is_logged_in:
            if not await self.login():
                return []

        all_slots = []
        target_months = sorted({d[:7] for d in target_dates})

        booking_types = ["reservation"]
        if nav.get("has_lottery"):
            booking_types.append("lottery")

        for booking_type in booking_types:
            bt_label = "予約" if booking_type == "reservation" else "抽選"
            areas = nav.get("areas", [])

            # エリアが未設定の場合は動的に発見する
            if not areas:
                self._log(f"エリア自動発見: {facility_id} / {bt_label}")
                areas = await self._discover_areas(facility_id, booking_type)
                if not areas:
                    self._log(f"エリアが見つかりません: {facility_id} / {bt_label}")
                    continue

            for area in areas:
                area_name = area["name"]
                self._log(f"チェック開始: {facility_id} / {area_name} / {bt_label}")
                if on_progress:
                    on_progress(f"[{bt_label}] {area_name} チェック開始")

                ok = await self._navigate_to_monthly_calendar(facility_id, area, booking_type)
                if not ok:
                    continue

                for month_str in target_months:
                    year, month = map(int, month_str.split("-"))
                    try:
                        current = await self._extract_monthly_calendar()
                        if current["year_month"] != month_str:
                            await self._move_to_month(year, month)
                            current = await self._extract_monthly_calendar()

                        self._log(f"  {month_str} {bt_label} 月間データ: {current['facility_name']}")

                        for date_str in target_dates:
                            if not date_str.startswith(month_str):
                                continue
                            day = str(int(date_str.split("-")[2]))
                            day_data = current["days"].get(day)
                            if not day_data:
                                continue

                            status = day_data["status"]
                            checked_at = datetime.utcnow().isoformat()

                            # 月レベルスロット（時間帯不明）
                            all_slots.append({
                                "facility_id": facility_id,
                                "area_name": area_name,
                                "date": date_str,
                                "time_start": "",
                                "time_end": "",
                                "status": status,
                                "status_raw": day_data["status_raw"],
                                "booking_href": day_data.get("href"),
                                "booking_type": booking_type,
                                "checked_at": checked_at,
                            })

                            # 空きがあれば週間ビューで時間帯を確認
                            if day_data["clickable"] and status in ("available", "partial"):
                                try:
                                    day_num = int(day)
                                    async with self.page.expect_navigation(wait_until="networkidle"):
                                        await self.page.evaluate(
                                            "d => { const a = [...document.querySelectorAll('a')]"
                                            ".find(a => { const t = a.innerText.trim();"
                                            " return t === d+'日' || t === String(d); });"
                                            " if(a) a.click(); else throw new Error('Day link not found: '+d); }",
                                            day_num,
                                        )
                                    weekly_slots = await self._extract_weekly_slots(facility_id, area_name)
                                    day_slots = [s for s in weekly_slots if s["date"] == date_str]
                                    for s in day_slots:
                                        s["booking_type"] = booking_type
                                    all_slots.extend(day_slots)
                                    self._log(f"    {date_str}: {len(day_slots)}スロット取得")
                                    # 月間ビューに戻る
                                    await self._navigate_to_monthly_calendar(facility_id, area, booking_type)
                                    await self._move_to_month(year, month)
                                except Exception as e:
                                    self._log(f"    週間ビューエラー ({date_str}): {e}")

                    except Exception as e:
                        self._log(f"月間データ取得エラー ({month_str}/{bt_label}): {e}")

        return all_slots

    # ──────────────────────────────────────────
    # フロー検出（Phase 1 互換）
    # ──────────────────────────────────────────

    async def detect_booking_flow(self, facility: dict) -> list[dict]:
        """予約フローを検出してステップリストを返す（Phase 1 互換）"""
        steps = []
        facility_id = facility["id"]

        await self.page.goto(TOP_URL, wait_until="networkidle")
        step = await self.capture_step(1, "トップ_ログインページ", facility_id)
        step["requires_login"] = False
        steps.append(step)

        login_ok = await self.login()
        if login_ok:
            step2 = await self.capture_step(2, "ログイン後_メニュー", facility_id)
            step2["requires_login"] = True
            steps.append(step2)

        for s in steps:
            if "detected_at" not in s:
                s["detected_at"] = datetime.utcnow().isoformat()
        return steps

    def _log(self, msg: str):
        self._flow_log.append({"time": datetime.utcnow().isoformat(), "msg": msg})
        print(f"[FureaiNet] {msg}")


# ──────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────

def _parse_date_header(text: str) -> str | None:
    """
    週間ビューの日付ヘッダーをパース。
    "4/1(火)" → "2026-04-01" (当年で解釈)
    """
    m = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        year = datetime.today().year
        # 月が現在より前の場合は翌年
        if month < datetime.today().month - 1:
            year += 1
        return f"{year}-{month:02d}-{day:02d}"
    return None


def get_target_dates(days_ahead: int = 60) -> list[str]:
    """今日から n 日分の日付リストを返す"""
    today = datetime.today()
    return [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_ahead)]
