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

# 既知のエンドポイント（Struts アクション）
ENDPOINTS = {
    "top": "/web/",
    "facility_search": "/web/rsvWTransEventSrchAction.do",
    "user_register": "/web/rsvWTransUserRegistAction.do",
    "inquiry": "/web/rsvPawnc1000DispAction.do",
    "faq": "/web/rsvPawqa1000DispAction.do",
}

# 施設種別コード（検索時に使用。実際の値はフロー検出で確認）
FACILITY_SEARCH_PARAMS = {
    "takatsu_sports_center": {
        "keywords": ["高津スポーツセンター", "高津", "二子"],
        "type_hint": "体育館",
    },
    "todoroki_tennis": {
        "keywords": ["等々力", "テニス"],
        "type_hint": "テニスコート",
    },
    "tama_sports_center": {
        "keywords": ["多摩スポーツセンター", "多摩"],
        "type_hint": "体育館",
    },
}


class FureaiNetScraper(BaseScraper):
    """ふれあいネット専用スクレイパー"""

    def __init__(self, headless: bool = True, user_id: str = "", password: str = "", security_no: str = ""):
        super().__init__(headless=headless)
        self.user_id = user_id
        self.password = password
        self.security_no = security_no
        self.is_logged_in = False
        self._flow_log: list[dict] = []  # デバッグ用のフロー記録

    # ──────────────────────────────────────────
    # ログイン
    # ──────────────────────────────────────────

    async def login(self) -> bool:
        """ログイン。成功したら True を返す。"""
        if not (self.user_id and self.password and self.security_no):
            return False

        await self.page.goto(TOP_URL, wait_until="networkidle")
        try:
            # ユーザーパスポート・パスワード・セキュリティ番号を入力
            await self.page.fill("input[name*='userId'], input[name*='user_id'], input#userId", self.user_id)
            await self.page.fill("input[name*='password'], input[type='password']", self.password)
            await self.page.fill("input[name*='security'], input[name*='Security']", self.security_no)
            await self.page.click("input[type='submit'], button[type='submit']")
            await self.page.wait_for_load_state("networkidle")

            # ログイン成功判定（ログアウトリンクが出たら成功）
            logout_visible = await self.page.locator("a:has-text('ログアウト'), a:has-text('logout')").count()
            self.is_logged_in = logout_visible > 0
            return self.is_logged_in
        except Exception as e:
            self._log(f"ログイン失敗: {e}")
            return False

    # ──────────────────────────────────────────
    # フロー検出（Phase 1 コア機能）
    # ──────────────────────────────────────────

    async def detect_booking_flow(self, facility: dict) -> list[dict]:
        """
        予約フローを検出してステップリストを返す。
        ログインなしで到達できる範囲と、ログイン後に変わる部分を記録する。
        """
        steps = []
        facility_id = facility["id"]

        # Step 1: トップページ
        await self.page.goto(TOP_URL, wait_until="networkidle")
        step = await self.capture_step(1, "トップ_ログインページ", facility_id)
        step["requires_login"] = False
        step["notes"] = "ユーザパスポート・パスワード・セキュリティ番号の3要素でログイン"
        steps.append(step)

        # Step 2: ログイン試行
        login_ok = await self.login()
        if login_ok:
            step2 = await self.capture_step(2, "ログイン後_トップ", facility_id)
            step2["requires_login"] = True
            step2["notes"] = "ログイン成功。ここから空き確認・予約が可能。"
            steps.append(step2)

        # Step 3: 施設検索ページ
        try:
            await self.page.goto(f"{BASE_URL}{ENDPOINTS['facility_search']}", wait_until="networkidle")
            step3 = await self.capture_step(3, "施設検索ページ", facility_id)
            step3["requires_login"] = not login_ok
            step3["notes"] = "施設種別・エリアの絞り込みフォームがある想定"
            steps.append(step3)

            # Step 4: 施設検索（対象施設を検索）
            search_config = FACILITY_SEARCH_PARAMS.get(facility_id, {})
            found_facility_url = await self._search_facility(search_config)

            if found_facility_url:
                await self.page.goto(found_facility_url, wait_until="networkidle")
                step4 = await self.capture_step(4, "施設詳細_空き確認", facility_id)
                step4["notes"] = "施設の空き状況カレンダーが表示される想定"
                steps.append(step4)

                # Step 5: カレンダー操作
                calendar_steps = await self._navigate_calendar(facility_id, step_offset=5)
                steps.extend(calendar_steps)

        except Exception as e:
            steps.append({
                "facility_id": facility_id,
                "step_number": len(steps) + 1,
                "step_name": "エラー",
                "url": self.page.url,
                "page_title": "",
                "form_fields": [],
                "navigation_links": [],
                "screenshot_path": "",
                "requires_login": True,
                "notes": f"ナビゲーション中にエラー: {e}",
                "detected_at": datetime.utcnow().isoformat(),
            })

        # 各ステップに検出日時を付加
        for s in steps:
            if "detected_at" not in s:
                s["detected_at"] = datetime.utcnow().isoformat()

        return steps

    async def _search_facility(self, search_config: dict) -> str | None:
        """施設検索フォームで対象施設を探し、詳細ページURLを返す"""
        keywords = search_config.get("keywords", [])
        type_hint = search_config.get("type_hint", "")

        # 検索フォームに施設名を入力して検索
        for keyword in keywords:
            try:
                search_input = self.page.locator(
                    "input[name*='facilityName'], input[name*='keyword'], input[type='text']"
                ).first
                if await search_input.count() > 0:
                    await search_input.fill(keyword)
                    await self.page.click("input[type='submit'], button[type='submit']")
                    await self.page.wait_for_load_state("networkidle")

                    # 検索結果リンクを探す
                    for kw in keywords:
                        links = await self.page.locator(f"a:has-text('{kw}')").all()
                        if links:
                            return await links[0].get_attribute("href")
            except Exception:
                continue

        # フォームがない場合、ページ内リンクから施設を探す
        all_links = await self.get_nav_links()
        for kw in keywords:
            for link in all_links:
                if kw in link["text"]:
                    return link["href"]

        return None

    async def _navigate_calendar(self, facility_id: str, step_offset: int = 5) -> list[dict]:
        """空き確認カレンダーを操作し、各状態をステップとして記録"""
        steps = []
        try:
            # 「空き確認」ボタンを探してクリック
            for selector in ["a:has-text('空き確認')", "a:has-text('予約')", "input[value*='空き']",
                              "button:has-text('空き')"]:
                btn = self.page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click()
                    await self.page.wait_for_load_state("networkidle")
                    step = await self.capture_step(step_offset, "空き状況カレンダー", facility_id)
                    step["notes"] = "空き○、使用済み×、抽選△ などの表記を確認"
                    steps.append(step)
                    break

            # 翌週へ進んで構造を確認
            for selector in ["a:has-text('次週')", "a:has-text('翌週')", "input[value*='次']"]:
                btn = self.page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click()
                    await self.page.wait_for_load_state("networkidle")
                    step = await self.capture_step(step_offset + 1, "カレンダー_翌週", facility_id)
                    step["notes"] = "翌週の空き状況。ページネーション方式を確認。"
                    steps.append(step)
                    break

        except Exception as e:
            steps.append({
                "facility_id": facility_id,
                "step_number": step_offset,
                "step_name": "カレンダーエラー",
                "url": self.page.url,
                "page_title": "",
                "form_fields": [],
                "navigation_links": [],
                "screenshot_path": "",
                "notes": f"カレンダー操作中にエラー: {e}",
            })

        return steps

    # ──────────────────────────────────────────
    # 空き状況チェック（Phase 1 でログイン可能な場合）
    # ──────────────────────────────────────────

    async def check_availability(self, facility: dict, target_dates: list[str]) -> list[dict]:
        """
        指定日付の空き状況を取得する。
        ログインなしでも閲覧できる範囲で取得。
        """
        slots = []
        facility_id = facility["id"]

        if not self.is_logged_in:
            await self.login()

        try:
            await self.page.goto(f"{BASE_URL}{ENDPOINTS['facility_search']}", wait_until="networkidle")
            found_url = await self._search_facility(FACILITY_SEARCH_PARAMS.get(facility_id, {}))

            if not found_url:
                return []

            await self.page.goto(found_url, wait_until="networkidle")

            for date_str in target_dates:
                day_slots = await self._extract_slots_for_date(facility_id, date_str)
                slots.extend(day_slots)

        except Exception as e:
            self._log(f"空き確認エラー ({facility_id}): {e}")

        return slots

    async def _extract_slots_for_date(self, facility_id: str, date_str: str) -> list[dict]:
        """特定日の空きスロットをカレンダーから抽出"""
        slots = []
        checked_at = datetime.utcnow().isoformat()

        try:
            # カレンダーから行（時間帯）とセル（状態）を抽出
            table_data = await self.page.evaluate("""
                (targetDate) => {
                    const results = [];
                    // カレンダーテーブルを探す
                    const tables = document.querySelectorAll('table');
                    tables.forEach(table => {
                        const rows = table.querySelectorAll('tr');
                        rows.forEach(row => {
                            const cells = row.querySelectorAll('td, th');
                            const rowText = Array.from(cells).map(c => c.innerText.trim());

                            // 時間帯行を特定（HH:MM パターン）
                            const timeMatch = rowText.find(t => /\\d{1,2}:\\d{2}/.test(t));
                            if (timeMatch) {
                                cells.forEach((cell, idx) => {
                                    const text = cell.innerText.trim();
                                    const className = cell.className;
                                    const bgColor = cell.style.backgroundColor;
                                    results.push({
                                        row_text: timeMatch,
                                        cell_index: idx,
                                        cell_text: text,
                                        class_name: className,
                                        bg_color: bgColor,
                                        link: cell.querySelector('a') ? cell.querySelector('a').href : null
                                    });
                                });
                            }
                        });
                    });
                    return results;
                }
            """, date_str)

            for cell in table_data:
                status = _parse_status(cell.get("cell_text", ""), cell.get("class_name", ""))
                time_parts = _parse_time_range(cell.get("row_text", ""))
                if time_parts:
                    slots.append({
                        "facility_id": facility_id,
                        "area_name": "",
                        "date": date_str,
                        "time_start": time_parts[0],
                        "time_end": time_parts[1],
                        "status": status,
                        "checked_at": checked_at,
                    })

        except Exception as e:
            self._log(f"スロット抽出エラー ({date_str}): {e}")

        return slots

    def _log(self, msg: str):
        self._flow_log.append({"time": datetime.utcnow().isoformat(), "msg": msg})
        print(f"[FureaiNet] {msg}")


# ──────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────

def _parse_status(text: str, class_name: str) -> str:
    """セルのテキスト・クラス名から空き状況を判定"""
    text = text.strip()
    class_name = class_name.lower()

    if text in ("○", "空", "空き") or "available" in class_name or "open" in class_name:
        return "available"
    if text in ("×", "×", "使用中", "予約済") or "occupied" in class_name or "full" in class_name:
        return "occupied"
    if text in ("△", "抽選", "抽"):
        return "lottery"
    if text in ("－", "休", "休館"):
        return "closed"
    return "unknown"


def _parse_time_range(text: str) -> tuple[str, str] | None:
    """'9:00～10:00' や '9:00-10:00' などを (start, end) にパース"""
    pattern = r"(\d{1,2}:\d{2})[～~\-〜](\d{1,2}:\d{2})"
    m = re.search(pattern, text)
    if m:
        return m.group(1), m.group(2)
    # 単一時刻のみの場合は1時間幅と仮定
    single = re.search(r"(\d{1,2}:\d{2})", text)
    if single:
        start = single.group(1)
        h, mn = map(int, start.split(":"))
        end_h = h + 1
        end = f"{end_h:02d}:{mn:02d}"
        return start, end
    return None


def get_target_dates(days_ahead: int = 14) -> list[str]:
    """今日から n 日分の日付リストを返す"""
    today = datetime.today()
    return [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_ahead)]
