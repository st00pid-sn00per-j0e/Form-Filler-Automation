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

    def open_page(self, url):
        """Open a new page and navigate to URL"""
        try:
            if not self.playwright:
                self.playwright = sync_playwright().start()
                self.browser = self.playwright.chromium.launch(headless=self.headless)
                self.context = self.browser.new_context(
                    viewport={'width': 1280, 'height': 800},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )

            page = self.context.new_page()
            page.goto(url, wait_until='networkidle', timeout=30000)
            return page
        except Exception as e:
            # Clean up on failure
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
            raise e

    def close(self):
        """Close browser and cleanup"""
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
