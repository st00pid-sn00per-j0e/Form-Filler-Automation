"""
Form submission handling
"""
import re
import time

class SubmitHandler:
    def __init__(self, page):
        self.page = page

    def find_and_click_submit(self):
        """
        Find and click submit button using multiple strategies
        """
        contexts = [self.page] + list(self.page.frames)

        # 1. Standard submit elements
        submit_selectors = [
            'input[type="submit"]',
            'button[type="submit"]',
            'input[type="image"][alt*="submit" i]',
            'button[type="button"]:has-text("Submit")',
            'button[type="button"]:has-text("Send")',
            'input[value*="Submit" i]',
            'input[value*="Send" i]',
        ]
        for ctx in contexts:
            for selector in submit_selectors:
                try:
                    locator = ctx.locator(selector).first
                    if locator.is_visible(timeout=1000):
                        locator.click()
                        time.sleep(2)
                        return True
                except Exception:
                    continue

        # 2. Role-based with common submit labels (exact and partial)
        submit_labels = [
            "Submit", "Send", "submit", "send",
            "Get in Touch", "Contact Us", "Send Message", "Request Quote",
            "Apply", "Register", "Sign Up", "Subscribe", "Submit Form"
        ]
        for text in submit_labels:
            for ctx in contexts:
                try:
                    locator = ctx.get_by_role("button", name=re.compile(re.escape(text), re.I))
                    if locator.first.is_visible(timeout=800):
                        locator.first.click()
                        time.sleep(2)
                        return True
                except Exception:
                    continue

        # 3. Links/divs inside forms that act as submit buttons
        for ctx in contexts:
            try:
                within_form = ctx.locator("form").first
                for text in ["Submit", "Send", "Get in Touch", "Contact Us", "Send Message"]:
                    try:
                        btn = within_form.get_by_role("button", name=re.compile(re.escape(text), re.I))
                        if btn.first.is_visible(timeout=500):
                            btn.first.click()
                            time.sleep(2)
                            return True
                    except Exception:
                        pass
                for text in ["Submit", "Send"]:
                    try:
                        link = within_form.locator(f'a:has-text("{text}")').first
                        if link.is_visible(timeout=500):
                            link.click()
                            time.sleep(2)
                            return True
                    except Exception:
                        pass
            except Exception:
                pass

        # 4. Any button in a form with submit-like text
        for ctx in contexts:
            try:
                form_buttons = ctx.locator("form button, form input[type='submit']")
                count = form_buttons.count()
                for i in range(min(count, 5)):
                    try:
                        el = form_buttons.nth(i)
                        if el.is_visible(timeout=500):
                            txt = (el.text_content() or el.get_attribute("value") or "").strip()
                            if re.search(r"submit|send|contact|apply|register", txt, re.I):
                                el.click()
                                time.sleep(2)
                                return True
                    except Exception:
                        continue
            except Exception:
                pass

        return False

    def check_success(self):
        """
        Check if form submission was successful
        """
        # Use specific confirmation phrases (avoid overly generic words like "success").
        success_patterns = [
            re.compile(r"thank(s)?\s+you", re.I),
            re.compile(r"thanks?\s+for\s+(contacting|reaching out)", re.I),
            re.compile(r"message\s+(has\s+been\s+)?(sent|submitted)", re.I),
            re.compile(r"(form|request|application)\s+(has\s+been\s+)?submitted", re.I),
            re.compile(r"(submission|request)\s+(has\s+been\s+)?received", re.I),
            re.compile(r"we('ll| will)\s+(be in touch|contact you|reach out)", re.I),
        ]

        for pattern in success_patterns:
            try:
                if self.page.get_by_text(pattern).first.is_visible(timeout=2000):
                    return True
            except Exception:
                continue

        # Check if redirected (e.g. httpbin /forms/post -> /post)
        current_url = self.page.url
        lowered_url = current_url.lower()
        if re.search(r"/(thank[-_]?you|thanks|submitted|confirmation)(/|$)", lowered_url):
            return True
        if re.search(r"/success(/|$)", lowered_url) and "success-stor" not in lowered_url:
            return True
        if '/post' in current_url and 'forms' not in current_url:  # httpbin form submit
            return True

        # Retry: wait for navigation that may be in progress
        try:
            self.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        current_url = self.page.url
        lowered_url = current_url.lower()
        if re.search(r"/(thank[-_]?you|thanks|submitted|confirmation)(/|$)", lowered_url):
            return True
        if re.search(r"/success(/|$)", lowered_url) and "success-stor" not in lowered_url:
            return True
        if '/post' in current_url and 'forms' not in current_url:
            return True

        return False
