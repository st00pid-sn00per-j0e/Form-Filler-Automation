"""
Form fill verification
"""
import re

class VerificationEngine:
    def __init__(self, page, screenshot_path):
        self.page = page
        self.screenshot_path = screenshot_path

    def verify_fill(self, element_info, expected_value):
        """
        Verify that a field was filled correctly
        """
        try:
            locator = self._resolve_locator(element_info)
            if locator is None:
                return False

            try:
                locator.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass

            actual_value = None
            dom_type = str(element_info.get('dom', {}).get('type', '')).lower()
            if dom_type == "select":
                try:
                    actual_value = locator.evaluate(
                        "el => (el.options && el.selectedIndex >= 0) ? (el.options[el.selectedIndex].text || el.value || '') : (el.value || '')"
                    )
                except Exception:
                    actual_value = None
            try:
                if actual_value is None:
                    actual_value = locator.input_value()
            except Exception:
                pass

            # contenteditable / textarea with innerText
            if not actual_value or (dom_type == 'textarea' and not actual_value.strip()):
                try:
                    actual_value = locator.evaluate("el => el.innerText || el.textContent || ''")
                except Exception:
                    pass

            return self._is_match(actual_value or "", expected_value)

        except Exception as e:
            print(f"Failed to verify field: {e}")
            return False

    def _resolve_locator(self, element_info):
        dom_info = element_info.get("dom", {})
        selector = (dom_info.get("selector") or "").strip()
        xpath = (dom_info.get("xpath") or "").strip()
        frame_url = (dom_info.get("frame_url") or "").strip()
        frame_name = (dom_info.get("frame_name") or "").strip()

        context = self.page
        if frame_url or frame_name:
            for fr in self.page.frames:
                if frame_url and fr.url == frame_url:
                    context = fr
                    break
                if frame_name and fr.name == frame_name:
                    context = fr
                    break

        if selector:
            try:
                loc = context.locator(selector).first
                if loc.count() > 0:
                    return loc
            except Exception:
                pass
        if xpath:
            try:
                loc = context.locator(f"xpath={xpath}").first
                if loc.count() > 0:
                    return loc
            except Exception:
                pass
        return None

    @staticmethod
    def _normalize_text(value):
        if value is None:
            return ""
        value = str(value).strip().lower()
        value = re.sub(r"\s+", " ", value)
        return value

    @staticmethod
    def _digits_only(value):
        return re.sub(r"\D", "", value or "")

    def _is_match(self, actual_value, expected_value):
        actual = self._normalize_text(actual_value)
        expected = self._normalize_text(expected_value)

        if not expected:
            return True  # No expected value means we're not verifying content
        if not actual:
            return False

        if actual == expected:
            return True

        # Accept common UI transformations: extra spaces, masks, prefixes
        if expected in actual or actual in expected:
            return True

        # Handle maxlength truncation: expected may be longer than actual
        if len(actual) >= 5 and expected[:len(actual)] == actual:
            return True
        if len(expected) >= 5 and actual[:len(expected)] == expected:
            return True

        # Handle phone formatting differences like +1 (234) 567-8900
        actual_digits = self._digits_only(actual)
        expected_digits = self._digits_only(expected)
        if len(expected_digits) >= 7 and expected_digits in actual_digits:
            return True
        if len(actual_digits) >= 7 and actual_digits in expected_digits:
            return True

        return False
