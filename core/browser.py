"""
Browser management using Playwright
"""
from playwright.sync_api import sync_playwright

class BrowserManager:
    def __init__(self, headless=False):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None

    def _cleanup(self):
        """Best-effort cleanup that also resets references."""
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass
        self.context = None
        self.browser = None
        self.playwright = None

    def _ensure_session(self):
        """Create a fresh Playwright session when none is active."""
        if self.playwright and self.browser and self.context:
            return
        self._cleanup()
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )

    def open_page(self, url):
        """Open a new page and navigate to URL"""
        last_error = None
        for _ in range(2):
            try:
                self._ensure_session()
                page = self.context.new_page()
                page.goto(url, wait_until='networkidle', timeout=30000)
                return page
            except Exception as e:
                last_error = e
                self._cleanup()
        raise last_error

    def close(self):
        """Close browser and cleanup"""
        self._cleanup()
