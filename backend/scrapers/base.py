class BaseScraper:
    """スクレイパー基底クラス（Playwright のライフサイクル管理のみ）"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.page = None

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        try:
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            context = await self.browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
            )
            self.page = await context.new_page()
            # ふれあいネットの一時的な応答遅延でハングまたは早期タイムアウトしないよう既定値を明示
            self.page.set_default_timeout(30000)
            self.page.set_default_navigation_timeout(45000)
            return self
        except Exception:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            raise

    async def __aexit__(self, *args):
        try:
            if self.browser:
                await self.browser.close()
        finally:
            if self.playwright:
                await self.playwright.stop()
