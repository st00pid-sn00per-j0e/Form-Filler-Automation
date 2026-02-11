"""
Form field filling logic
"""
import time
import re

class FormFiller:
    def __init__(self, page):
        self.page = page

    def fill_field(self, element_info, value):
        """
        Fill a form field with retry logic
        """
        try:
            locator = self._resolve_locator(element_info)
            if locator is None:
                return False

            dom_info = element_info.get('dom', {})
            attrs = dom_info.get('attributes', {})
            input_type = str(attrs.get('type', '')).lower()
            dom_type = str(dom_info.get('type', '')).lower()

            # Bring element into viewport before interaction.
            self._scroll_into_view(locator)

            # Wait for element to be visible/interactable
            locator.wait_for(state='visible', timeout=5000)

            # Handle non-text inputs explicitly.
            if input_type in ['checkbox', 'radio']:
                if str(value).strip().lower() in ['1', 'true', 'yes', 'on', 'checked']:
                    locator.check(timeout=4000)
                else:
                    locator.click(timeout=4000)
                time.sleep(0.2)
                return True

            if dom_type == 'select':
                if self._select_option(locator, value):
                    time.sleep(0.2)
                    return True
                return False

            # Check for contenteditable (rich text editors)
            is_contenteditable = locator.evaluate(
                "el => el.isContentEditable || el.getAttribute('contenteditable') === 'true'"
            )
            if is_contenteditable:
                return self._fill_contenteditable(locator, value)

            # Clear existing content when possible.
            try:
                locator.clear(timeout=3000)
            except Exception:
                try:
                    locator.fill("", timeout=3000)
                except Exception:
                    pass  # Some fields don't support clear; try fill anyway

            # Tel fields are often masked and more reliable with keyboard typing.
            if input_type in ['tel', 'phone']:
                if not self.handle_input_mask(locator, value, "phone"):
                    if not self._type_phone(locator, value):
                        return False
            elif input_type in ['date']:
                if not self.handle_input_mask(locator, value, "date"):
                    return False
            else:
                # Prefer fill; fallback to type for stubborn/React-controlled fields.
                filled = False
                for attempt in range(2):
                    try:
                        locator.fill(str(value), timeout=5000)
                        filled = True
                        break
                    except Exception:
                        try:
                            locator.click(timeout=3000)
                            locator.type(str(value), delay=50, timeout=5000)
                            filled = True
                            break
                        except Exception:
                            if attempt == 0:
                                locator.focus(timeout=2000)
                            pass
                if not filled:
                    return False

            # Small delay to ensure typing is complete
            time.sleep(0.3)

            return True

        except Exception as e:
            print(f"Failed to fill field: {e}")
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

    def _select_option(self, locator, value):
        value_str = str(value).strip()
        if not value_str:
            return False
        try:
            locator.select_option(label=value_str, timeout=5000)
            return True
        except Exception:
            pass
        try:
            locator.select_option(value=value_str, timeout=5000)
            return True
        except Exception:
            pass
        try:
            options = locator.locator("option")
            count = options.count()
            best_value = None
            target = value_str.lower()
            for i in range(count):
                opt = options.nth(i)
                opt_label = (opt.text_content(timeout=1000) or "").strip()
                opt_value = (opt.get_attribute("value", timeout=1000) or "").strip()
                if opt_label.lower() == target or opt_value.lower() == target:
                    best_value = opt_value or opt_label
                    break
                if target in opt_label.lower() or target in opt_value.lower():
                    best_value = opt_value or opt_label
            if best_value is not None:
                locator.select_option(value=best_value, timeout=5000)
                return True
        except Exception:
            pass
        return False

    def _type_phone(self, locator, value):
        raw = str(value).strip()
        if not raw:
            return False
        try:
            locator.click(timeout=3000)
            locator.press("Control+A", timeout=2000)
            locator.press("Delete", timeout=2000)
            locator.type(raw, delay=40, timeout=5000)
            return True
        except Exception:
            pass
        # Fallback to digits-only for strict masks.
        try:
            digits = re.sub(r"\D", "", raw)
            if not digits:
                return False
            locator.click(timeout=3000)
            locator.press("Control+A", timeout=2000)
            locator.press("Delete", timeout=2000)
            locator.type(digits, delay=40, timeout=5000)
            return True
        except Exception:
            return False

    def handle_input_mask(self, locator, value, mask_type):
        """Handle common masks (phone/date/zip/ssn) before generic typing."""
        masks = {
            "phone": r"(\d{3})(\d{3})(\d{4})",
            "date": r"(\d{2})(\d{2})(\d{4})",
            "ssn": r"(\d{3})(\d{2})(\d{4})",
            "zip": r"(\d{5})(\d{4})?",
        }
        if mask_type not in masks:
            return False

        raw = str(value or "").strip()
        digits = re.sub(r"\D", "", raw)
        if not digits:
            return False

        try:
            locator.click(timeout=3000)
            locator.press("Control+A", timeout=2000)
            locator.press("Delete", timeout=2000)

            if mask_type == "phone":
                digits = digits[-10:]
                if len(digits) == 10:
                    formatted = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
                    locator.type(formatted, delay=35, timeout=5000)
                    return True
                locator.type(digits, delay=35, timeout=5000)
                return True

            if mask_type == "date" and len(digits) >= 8:
                formatted = f"{digits[:2]}/{digits[2:4]}/{digits[4:8]}"
                locator.type(formatted, delay=35, timeout=5000)
                return True

            locator.type(digits, delay=35, timeout=5000)
            return True
        except Exception:
            return False

    def _fill_contenteditable(self, locator, value):
        """Fill contenteditable div/span (rich text editors)."""
        try:
            locator.click(timeout=3000)
            locator.press("Control+A", timeout=2000)
            locator.press("Backspace", timeout=2000)
            locator.type(str(value), delay=30, timeout=5000)
            return True
        except Exception:
            try:
                locator.evaluate(
                    """(el, val) => {
                        el.focus();
                        el.innerHTML = val;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                    }""",
                    str(value)
                )
                return True
            except Exception:
                return False

    def _scroll_into_view(self, locator):
        """Ensure target element is in view for reliable fill and OCR-related visual flow."""
        try:
            locator.scroll_into_view_if_needed(timeout=3000)
            return
        except Exception:
            pass

        # JS fallback helps with nested/overflow containers.
        try:
            locator.evaluate(
                "el => el.scrollIntoView({behavior:'instant', block:'center', inline:'nearest'})"
            )
        except Exception:
            pass
