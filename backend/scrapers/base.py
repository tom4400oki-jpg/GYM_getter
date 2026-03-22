from abc import ABC, abstractmethod
from pathlib import Path
import os


class BaseScraper(ABC):
    """スクレイパー基底クラス"""

    BASE_SCREENSHOT_DIR = Path("screenshots")

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.page = None
        self.BASE_SCREENSHOT_DIR.mkdir(exist_ok=True)

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        context = await self.browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        self.page = await context.new_page()
        return self

    async def __aexit__(self, *args):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def screenshot(self, name: str) -> str:
        path = self.BASE_SCREENSHOT_DIR / f"{name}.png"
        await self.page.screenshot(path=str(path), full_page=True)
        return str(path)

    async def get_form_fields(self) -> list[dict]:
        """現在のページのフォーム要素をすべて抽出"""
        return await self.page.evaluate("""
            () => {
                const fields = [];
                document.querySelectorAll('input, select, textarea, button').forEach(el => {
                    const opts = el.tagName === 'SELECT'
                        ? Array.from(el.options).map(o => ({ value: o.value, text: o.text.trim() }))
                        : [];
                    fields.push({
                        tag: el.tagName.toLowerCase(),
                        name: el.name || '',
                        id: el.id || '',
                        type: el.type || '',
                        value: el.value || '',
                        placeholder: el.placeholder || '',
                        required: el.required,
                        disabled: el.disabled,
                        text: el.tagName === 'BUTTON' || el.type === 'submit'
                            ? (el.innerText || el.value || '').trim()
                            : '',
                        options: opts
                    });
                });
                return fields;
            }
        """)

    async def get_nav_links(self) -> list[dict]:
        """現在のページのリンクを抽出"""
        return await self.page.evaluate("""
            () => {
                const links = [];
                document.querySelectorAll('a[href]').forEach(el => {
                    const text = el.innerText.trim();
                    if (text) {
                        links.push({ text, href: el.href });
                    }
                });
                return links;
            }
        """)

    async def capture_step(self, step_number: int, step_name: str, facility_id: str) -> dict:
        """現在のページをステップとして記録"""
        url = self.page.url
        title = await self.page.title()
        form_fields = await self.get_form_fields()
        nav_links = await self.get_nav_links()
        shot = await self.screenshot(f"{facility_id}_step{step_number:02d}_{step_name}")

        return {
            "facility_id": facility_id,
            "step_number": step_number,
            "step_name": step_name,
            "url": url,
            "page_title": title,
            "form_fields": form_fields,
            "navigation_links": nav_links,
            "screenshot_path": shot,
        }

    @abstractmethod
    async def detect_booking_flow(self, facility: dict) -> list[dict]:
        """予約フローの各ステップを検出してリストで返す"""
        pass

    @abstractmethod
    async def check_availability(self, facility: dict, target_dates: list[str]) -> list[dict]:
        """空き状況を確認してスロットリストで返す"""
        pass
