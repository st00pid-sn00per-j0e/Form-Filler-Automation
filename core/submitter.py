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
        for selector in submit_selectors:
            try:
                locator = self.page.locator(selector).first
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
            try:
                locator = self.page.get_by_role("button", name=re.compile(re.escape(text), re.I))
                if locator.first.is_visible(timeout=800):
                    locator.first.click()
                    time.sleep(2)
                    return True
            except Exception:
                continue

        # 3. Links/divs inside forms that act as submit buttons
        try:
            within_form = self.page.locator("form").first
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
        try:
            form_buttons = self.page.locator("form button, form input[type='submit']")
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
        # Check for success indicators
        success_indicators = [
            'text=Thank you',
            'text=Thank You',
            'text=Success',
            'text=Submitted',
            'text=Sent successfully',
            'text=Message sent',
            'text=We\'ll be in touch',
            'text=We will contact you',
            'text=form submitted'
        ]

        for indicator in success_indicators:
            try:
                if self.page.locator(indicator).first.is_visible(timeout=2000):
                    return True
            except Exception:
                continue

        # Check if redirected (e.g. httpbin /forms/post -> /post)
        current_url = self.page.url
        if 'success' in current_url.lower() or 'thank' in current_url.lower():
            return True
        if '/post' in current_url and 'forms' not in current_url:  # httpbin form submit
            return True

        # Retry: wait for navigation that may be in progress
        try:
            self.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        current_url = self.page.url
        if 'success' in current_url.lower() or 'thank' in current_url.lower():
            return True
        if '/post' in current_url and 'forms' not in current_url:
            return True

        return False
