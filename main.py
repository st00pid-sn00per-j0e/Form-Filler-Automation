"""
Complete orchestration with all fixes applied
"""
import csv
import json
import os
import re
import time
from collections import Counter
from datetime import datetime
import cv2
import yaml
from core.browser import BrowserManager
from core.vision import UIElementDetector
from core.ocr import TextExtractor
from core.dom_mapper import DOMMapper
from core.field_classifier import FieldClassifier
from core.filler import FormFiller
from core.verifier import VerificationEngine
from core.submitter import SubmitHandler
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('automation.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class AutomatedFormFiller:
    def __init__(self, prefill_data_path="prefill_data.json", config_path="config.yaml"):
        with open(prefill_data_path) as f:
            self.prefill_data = json.load(f)
        self.config = self._load_config(config_path)
        ocr_cfg = self.config.get("ocr", {})
        output_cfg = self.config.get("output", {})

        self.browser = BrowserManager(headless=False)
        self.detector = UIElementDetector()
        self.ocr = TextExtractor(
            lang=ocr_cfg.get("language", "eng"),
            min_confidence=int(ocr_cfg.get("min_confidence", 50))
        )
        self.live_ocr_enabled = bool(ocr_cfg.get("live_trace", True))
        self.live_preview_chars = int(ocr_cfg.get("live_trace_preview_chars", 100))
        self.live_ocr_dir = output_cfg.get("live_ocr_dir", os.path.join("logs", "live_ocr"))
        self.annotated_screenshots_enabled = bool(output_cfg.get("save_annotated_screenshots", True))
        self.annotated_screenshots_dir = output_cfg.get(
            "annotated_screenshots_dir",
            os.path.join("logs", "annotated_screenshots")
        )
        configured_results = output_cfg.get("results_file", "results.csv")
        if os.path.isabs(configured_results):
            self.results_output_path = configured_results
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            preferred_form_dir = os.path.join(script_dir, "Form")
            if configured_results.lower() == "results.csv" and os.path.isdir(preferred_form_dir):
                self.results_output_path = os.path.join(preferred_form_dir, "results.csv")
            else:
                self.results_output_path = os.path.join(script_dir, configured_results)
        os.makedirs(os.path.dirname(self.results_output_path), exist_ok=True)
        os.makedirs(self.live_ocr_dir, exist_ok=True)
        os.makedirs(self.annotated_screenshots_dir, exist_ok=True)
        self.results = []
        self.captcha_detected = False

    def process_url(self, url):
        """Process a single URL with proper error handling"""
        result = {
            "URL": url,
            "Submission": "unsuccessful",
            "Reason": "unknown error",
            "timestamp": datetime.now().isoformat(),
            "fields_filled": 0,
            "total_fields": 0,
            "captcha_detected": False,
            "processing_time": 0,
            "monitor_issue": "",
            "monitor_summary": "",
            "elements_seen": 0,
            "elements_ready_to_fill": 0,
            "fill_attempts": 0,
            "fill_action_failed": 0,
            "fill_verify_failed": 0,
            "low_confidence_skips": 0,
            "non_fillable_skips": 0,
            "prefill_miss_skips": 0
        }

        start_time = time.time()
        page = None
        live_trace = {
            "url": url,
            "timestamp": datetime.now().isoformat(),
            "captcha_detected": False,
            "initial_screenshot": "",
            "annotated_screenshot": "",
            "post_fill_screenshot": "",
            "annotated_post_fill_screenshot": "",
            "post_submit_screenshot": "",
            "fields": [],
            "submit": {
                "attempted": False,
                "clicked": False,
                "dom_success": False,
                "ocr_success": False,
                "ocr_failure": False,
                "current_url": "",
                "ocr_excerpt": "",
                "ocr_success_matches": [],
                "ocr_failure_matches": []
            }
        }

        try:
            logger.info(f"Processing: {url}")

            # 1. Open page
            page = self.browser.open_page(url)
            time.sleep(2)

            # 2. Check for obvious captcha early
            if self._has_captcha(page):
                result["captcha_detected"] = True
                live_trace["captcha_detected"] = True
                logger.warning(f"Captcha detected on {url} (continuing until obstruction)")

            # 3. Capture screenshot
            screenshot_path = self._build_screenshot_path(url)
            shot_meta = self._capture_form_preferred_screenshot(page, screenshot_path)
            live_trace["initial_screenshot"] = screenshot_path
            live_trace["screenshot_mode"] = shot_meta.get("mode", "full")
            live_trace["screenshot_origin_px"] = list(shot_meta.get("origin_px", (0, 0)))
            live_trace["form_bbox"] = shot_meta.get("form_bbox")

            # 4. Detect UI elements
            boxes = self.detector.detect_form_elements(screenshot_path)

            # 5. Map to DOM elements (hybrid approach)
            elements = DOMMapper.find_form_elements(
                page,
                screenshot_path,
                boxes,
                screenshot_origin_px=shot_meta.get("origin_px", (0, 0))
            )

            # 6. Classify and fill fields
            filler = FormFiller(page)
            verifier = VerificationEngine(page, screenshot_path)

            fillable_elements = []
            for idx, element in enumerate(elements):
                xpath = element.get('dom', {}).get('xpath', '')
                # Extract text with confidence (OCR); fallback to "" if Tesseract unavailable
                try:
                    text, confidence = self.ocr.extract_with_context(
                        screenshot_path,
                        element['box']
                    )
                except Exception as e:
                    logger.warning(f"OCR failed for element {xpath or '[no-xpath]'}: {e}")
                    text, confidence = "", 0

                # Classify field (works with attributes when OCR confidence is low)
                adv = self.config.get("advanced", {})
                field_type, field_confidence = FieldClassifier.classify(
                    text,
                    element['dom']['type'],
                    element['dom'].get('attributes', {}),
                    use_minilm=bool(adv.get("use_minilm", True)),
                    log_unknown=bool(adv.get("log_unknown_patterns", True)),
                )
                live_entry = {
                    "index": idx,
                    "xpath": xpath,
                    "dom_type": element.get("dom", {}).get("type", ""),
                    "attributes": element.get("dom", {}).get("attributes", {}),
                    "box": [int(v) for v in element.get("box", (0, 0, 0, 0))],
                    "source": element.get("detection_source", "unknown"),
                    "ocr_confidence": float(confidence),
                    "ocr_text": text[:500],
                    "classified_as": field_type,
                    "classification_confidence": float(field_confidence),
                    "status": "classified",
                    "reason": ""
                }
                live_trace["fields"].append(live_entry)
                logger.info(
                    f"[LIVE OCR] idx={idx} field={field_type} "
                    f"ocr_conf={confidence:.1f} class_conf={field_confidence} "
                    f"text='{self._clip(text, self.live_preview_chars)}'"
                )

                # Require some classification confidence (from OCR or attributes)
                if field_confidence < 40 and confidence < 20:
                    live_entry["status"] = "skipped"
                    live_entry["reason"] = "low confidence"
                    result["low_confidence_skips"] += 1
                    logger.info(
                        f"[SKIP] low-confidence element {xpath or '[no-xpath]'} "
                        f"(field={field_type}, field_conf={field_confidence}, ocr_conf={confidence:.1f})"
                    )
                    continue

                # Check for captcha (hard stop)
                if field_type == "captcha":
                    result["captcha_detected"] = True
                    live_trace["captcha_detected"] = True
                    live_entry["status"] = "skipped"
                    live_entry["reason"] = "captcha field"
                    logger.warning(f"Captcha field detected on {url} (skipping captcha field)")
                    continue

                # Skip non-fillable fields
                if FieldClassifier.should_skip_field(field_type, element):
                    live_entry["status"] = "skipped"
                    live_entry["reason"] = "non-fillable"
                    result["non_fillable_skips"] += 1
                    logger.info(f"[SKIP] non-fillable field {field_type} at {xpath or '[no-xpath]'}")
                    continue

                # Only proceed if we have data for this field
                resolved_value = self._resolve_prefill_value(
                    field_type,
                    element.get("dom", {}).get("attributes", {})
                )
                if resolved_value is not None:
                    element['classified_as'] = field_type
                    element['classification_confidence'] = field_confidence
                    element['resolved_value'] = resolved_value
                    element['trace_index'] = idx
                    live_entry["status"] = "ready_to_fill"
                    live_entry["reason"] = "has prefill value"
                    fillable_elements.append(element)
                else:
                    live_entry["status"] = "skipped"
                    live_entry["reason"] = "no prefill data"
                    result["prefill_miss_skips"] += 1
                    logger.info(
                        f"[SKIP] no prefill data for classified field {field_type} at {xpath or '[no-xpath]'}"
                    )

            annotated = self._save_annotated_screenshot(
                screenshot_path,
                live_trace["fields"]
            )
            if annotated:
                live_trace["annotated_screenshot"] = annotated

            # Update metrics
            result["total_fields"] = len(fillable_elements)
            result["elements_seen"] = len(live_trace["fields"])
            result["elements_ready_to_fill"] = len(fillable_elements)

            # 7. Fill fields with retry logic
            filled_count = 0
            for element in fillable_elements:
                field_type = element['classified_as']
                value = element.get("resolved_value", "")
                trace_idx = element.get("trace_index")
                trace_ref = self._field_trace_ref(live_trace["fields"], trace_idx)
                result["fill_attempts"] += 1

                # Fill the field
                if filler.fill_field(element, value):
                    # Verify fill
                    if verifier.verify_fill(element, value):
                        filled_count += 1
                        if trace_ref is not None:
                            trace_ref["status"] = "filled"
                            trace_ref["reason"] = "dom verification passed"
                        logger.info(f"[OK] Filled {field_type}: {value[:30]}...")
                    else:
                        element['failed_attempts'] = element.get('failed_attempts', 0) + 1
                        result["fill_verify_failed"] += 1
                        if trace_ref is not None:
                            trace_ref["status"] = "fill_failed"
                            trace_ref["reason"] = "verification failed"
                        logger.warning(f"[X] Failed to fill {field_type}")
                else:
                    element['failed_attempts'] = element.get('failed_attempts', 0) + 1
                    result["fill_action_failed"] += 1
                    if trace_ref is not None:
                        trace_ref["status"] = "fill_failed"
                        trace_ref["reason"] = "fill action failed"

            # 7b. Detect and process dynamic fields that appear after interactions.
            dynamic_elements, dynamic_shot = self.detect_dynamic_fields(
                page,
                url,
                seen_elements=elements + fillable_elements
            )
            if dynamic_elements:
                verifier_dynamic = VerificationEngine(page, dynamic_shot)
                base_idx = len(live_trace["fields"])
                for offset, element in enumerate(dynamic_elements):
                    idx = base_idx + offset
                    xpath = element.get('dom', {}).get('xpath', '')
                    try:
                        text, confidence = self.ocr.extract_with_context(dynamic_shot, element['box'])
                    except Exception:
                        text, confidence = "", 0

                    adv = self.config.get("advanced", {})
                    field_type, field_confidence = FieldClassifier.classify(
                        text,
                        element['dom']['type'],
                        element['dom'].get('attributes', {}),
                        use_minilm=bool(adv.get("use_minilm", True)),
                        log_unknown=bool(adv.get("log_unknown_patterns", True)),
                    )
                    entry = {
                        "index": idx,
                        "xpath": xpath,
                        "dom_type": element.get("dom", {}).get("type", ""),
                        "attributes": element.get("dom", {}).get("attributes", {}),
                        "box": [int(v) for v in element.get("box", (0, 0, 0, 0))],
                        "source": element.get("detection_source", "dynamic"),
                        "ocr_confidence": float(confidence),
                        "ocr_text": text[:500],
                        "classified_as": field_type,
                        "classification_confidence": float(field_confidence),
                        "status": "classified",
                        "reason": "dynamic field",
                    }
                    live_trace["fields"].append(entry)
                    if FieldClassifier.should_skip_field(field_type, element):
                        entry["status"] = "skipped"
                        entry["reason"] = "non-fillable dynamic field"
                        result["non_fillable_skips"] += 1
                        continue

                    value = self._resolve_prefill_value(field_type, element.get("dom", {}).get("attributes", {}))
                    if value is None:
                        entry["status"] = "skipped"
                        entry["reason"] = "no prefill data"
                        result["prefill_miss_skips"] += 1
                        continue

                    entry["status"] = "ready_to_fill"
                    result["fill_attempts"] += 1
                    if filler.fill_field(element, value) and verifier_dynamic.verify_fill(element, value):
                        filled_count += 1
                        entry["status"] = "filled"
                        entry["reason"] = "dynamic fill verified"
                    else:
                        entry["status"] = "fill_failed"
                        entry["reason"] = "dynamic fill failed"
                        result["fill_action_failed"] += 1

            result["fields_filled"] = filled_count
            post_fill_shot = self._build_screenshot_path(url, "post_fill")
            self._capture_form_preferred_screenshot(page, post_fill_shot)
            live_trace["post_fill_screenshot"] = post_fill_shot
            annotated_post = self._save_annotated_screenshot(
                post_fill_shot,
                live_trace["fields"],
                suffix="post_fill"
            )
            if annotated_post:
                live_trace["annotated_post_fill_screenshot"] = annotated_post

            # 8. Submit form if we filled at least one field
            if filled_count > 0:
                submitter = SubmitHandler(page)
                live_trace["submit"]["attempted"] = True
                if submitter.find_and_click_submit():
                    live_trace["submit"]["clicked"] = True
                    time.sleep(3)  # Allow redirect/navigation to complete
                    # Enhanced success detection
                    dom_success = submitter.check_success()
                    live_trace["submit"]["dom_success"] = dom_success
                    post_submit_shot = self._build_screenshot_path(url, "post_submit")
                    self._capture_form_preferred_screenshot(page, post_submit_shot)
                    live_trace["post_submit_screenshot"] = post_submit_shot
                    ocr_signal = self._post_submit_ocr_signal(post_submit_shot)
                    ocr_success = bool(ocr_signal.get("success"))
                    ocr_failure = bool(ocr_signal.get("failure"))
                    ocr_excerpt = ocr_signal.get("excerpt", "")
                    live_trace["submit"]["ocr_success"] = ocr_success
                    live_trace["submit"]["ocr_failure"] = ocr_failure
                    live_trace["submit"]["ocr_excerpt"] = ocr_excerpt
                    live_trace["submit"]["ocr_success_matches"] = ocr_signal.get("success_matches", [])
                    live_trace["submit"]["ocr_failure_matches"] = ocr_signal.get("failure_matches", [])
                    live_trace["submit"]["current_url"] = page.url

                    if dom_success or ocr_success:
                        result["Submission status"] = "success"
                        result["reason"] = "form submitted successfully"
                        logger.info(f"[OK] Successfully submitted {url}")
                    elif ocr_failure:
                        failure_matches = ocr_signal.get("failure_matches", [])
                        if any("captcha" in m for m in failure_matches):
                            result["reason"] = "captcha verification blocked submission"
                        elif failure_matches:
                            result["reason"] = (
                                f"form submission failed validation checks ({failure_matches[0]})"
                            )
                        else:
                            result["reason"] = "form submission failed validation checks"
                        logger.warning(f"[!] Submission rejected for {url}")
                    else:
                        result["Submission status"] = "uncertain"
                        result["reason"] = (
                            "submission attempted; OCR screenshot had no clear success/failure signal"
                        )
                        logger.warning(f"[!] Submission uncertain for {url}")
                else:
                    if result["captcha_detected"]:
                        result["reason"] = "captcha likely obstructed submit button"
                    else:
                        result["reason"] = "could not find submit button"
                    logger.warning(f"[X] No submit button found on {url}")
            else:
                if result["captcha_detected"]:
                    result["reason"] = "captcha obstructed interaction or no fillable fields found"
                else:
                    result["reason"] = "no fillable fields found"
                logger.info(f"[i] No fillable fields on {url}")

        except Exception as e:
            result["reason"] = f"error: {str(e)[:100]}"
            logger.error(f"Error processing {url}: {str(e)}")

        finally:
            if page:
                try:
                    page.close()
                except Exception as e:
                    logger.warning(f"Failed to close page cleanly: {e}")

            result["processing_time"] = time.time() - start_time
            diagnostics = self._build_monitoring_diagnostics(live_trace, result)
            result.update(diagnostics)
            self.results.append(result)
            self._write_live_trace(live_trace)
            self.save_results()

            # Log result
            status_icon = "[SUCCESS]" if result["Submission status"] == "success" else "[FAILED]"
            logger.info(f"{status_icon} {url}: {result['reason']} ({result['processing_time']:.1f}s)")
            logger.info(
                "[MONITOR] issue=%s summary=%s",
                result.get("monitor_issue", ""),
                result.get("monitor_summary", "")
            )

        return result

    def _has_captcha(self, page):
        """Early captcha detection"""
        captcha_selectors = [
            'iframe[src*="recaptcha"]',
            'div[class*="captcha"]',
            'div[class*="g-recaptcha"]',
            'img[src*="captcha"]'
        ]

        for selector in captcha_selectors:
            if page.is_visible(selector, timeout=1000):
                return True

        return False

    def _resolve_prefill_value(self, field_type, attributes):
        # Direct key + case-insensitive/normalized lookup.
        prefill_norm = {}
        for k, v in (self.prefill_data or {}).items():
            if v is None:
                continue
            key = re.sub(r"[^a-z0-9]+", "", str(k).strip().lower())
            prefill_norm[key] = v

        if field_type in self.prefill_data:
            return self.prefill_data[field_type]
        # Alias lookups for common form field variations
        if field_type == "subject":
            return (
                self.prefill_data.get("subject")
                or self.prefill_data.get("topic")
                or self.prefill_data.get("title")
                or prefill_norm.get("subject")
                or prefill_norm.get("topic")
            )
        if field_type == "message":
            return (
                self.prefill_data.get("message")
                or self.prefill_data.get("comment")
                or self.prefill_data.get("comments")
                or prefill_norm.get("message")
            )
        if field_type in ("company", "company_name"):
            return (
                self.prefill_data.get("company")
                or self.prefill_data.get("company_name")
                or prefill_norm.get("company")
                or prefill_norm.get("companyname")
            )
        if field_type == "dropdown":
            # Generic dropdown fallback: prefer explicit keys.
            attrs = {str(k).lower(): str(v).lower() for k, v in (attributes or {}).items() if v}
            attr_blob = " ".join([
                attrs.get("name", ""),
                attrs.get("id", ""),
                attrs.get("placeholder", ""),
                attrs.get("aria-label", ""),
                attrs.get("label_text", ""),
                attrs.get("nearby_text", "")
            ])
            if any(tok in attr_blob for tok in ["country", "nation"]):
                return (
                    self.prefill_data.get("country")
                    or self.prefill_data.get("Country")
                    or prefill_norm.get("country")
                )
            if any(tok in attr_blob for tok in ["hear", "source", "referral", "how did you hear"]):
                return (
                    self.prefill_data.get("where_did_you_hear_about_us")
                    or self.prefill_data.get("Where did you hear about us")
                    or prefill_norm.get("wheredidyouhearaboutus")
                )
            return None

        attrs = {str(k).lower(): str(v).lower() for k, v in (attributes or {}).items() if v}
        full_name = (self.prefill_data.get("full_name") or "").strip()
        first_name = (self.prefill_data.get("first_name") or "").strip()
        last_name = (self.prefill_data.get("last_name") or "").strip()

        if field_type == "name":
            if full_name:
                return full_name
            if first_name or last_name:
                return f"{first_name} {last_name}".strip()
            return None

        if field_type == "first_name":
            if first_name:
                return first_name
            if full_name:
                return full_name.split()[0]
            return None

        if field_type == "last_name":
            if last_name:
                return last_name
            if full_name and len(full_name.split()) > 1:
                return full_name.split()[-1]
            return None

        # Fallback by attribute hints for mixed "name" keys.
        attr_blob = " ".join([
            attrs.get("name", ""),
            attrs.get("id", ""),
            attrs.get("placeholder", ""),
            attrs.get("aria-label", "")
        ])
        if "first" in attr_blob and (first_name or full_name):
            return first_name or full_name.split()[0]
        if ("last" in attr_blob or "surname" in attr_blob) and (last_name or full_name):
            return last_name or (full_name.split()[-1] if len(full_name.split()) > 1 else full_name)

        return None

    def detect_dynamic_fields(self, page, url, seen_elements=None, after_action=None):
        """
        Detect fields that appear after interaction and return only new ones.
        """
        seen_elements = seen_elements or []
        seen_keys = {self._element_identity(e) for e in seen_elements}

        initial_count = self._count_dom_fields(page)
        if after_action:
            try:
                after_action()
            except Exception:
                pass
            time.sleep(1)
        else:
            time.sleep(0.5)

        current_count = self._count_dom_fields(page)
        if current_count <= initial_count:
            return [], ""

        screenshot_path = self._build_screenshot_path(url, "dynamic")
        shot_meta = self._capture_form_preferred_screenshot(page, screenshot_path)
        boxes = self.detector.detect_form_elements(screenshot_path)
        discovered = DOMMapper.find_form_elements(
            page,
            screenshot_path,
            boxes,
            screenshot_origin_px=shot_meta.get("origin_px", (0, 0)),
        )
        new_elements = [e for e in discovered if self._element_identity(e) not in seen_keys]
        if new_elements:
            logger.info(f"Detected {len(new_elements)} dynamic fields")
        return new_elements, screenshot_path

    def _count_dom_fields(self, page):
        count = 0
        selector = "input, textarea, select, [contenteditable='true']"
        for frame in page.frames:
            try:
                count += len(frame.query_selector_all(selector))
            except Exception:
                continue
        return count

    def _element_identity(self, element):
        dom = element.get("dom", {}) if isinstance(element, dict) else {}
        return (
            dom.get("frame_url", ""),
            dom.get("frame_name", ""),
            dom.get("selector", ""),
            dom.get("xpath", ""),
            dom.get("type", ""),
        )

    def _sanitize_site_name(self, url):
        host = re.sub(r"^https?://", "", (url or "").strip(), flags=re.IGNORECASE)
        host = host.split("/", 1)[0].split(":", 1)[0]
        safe_host = re.sub(r"[^a-zA-Z0-9]+", "_", host).strip("_").lower()
        return safe_host or "site"

    def _build_screenshot_path(self, url, stage=""):
        os.makedirs("screenshots", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        site = self._sanitize_site_name(url)
        suffix = f"_{stage}" if stage else ""
        return f"screenshots/{ts}_{site}{suffix}.png"

    def _load_config(self, config_path):
        if not config_path or not os.path.exists(config_path):
            return {}
        try:
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"Failed to load config {config_path}: {e}")
            return {}

    def _clip(self, text, max_len=100):
        if not text:
            return ""
        return text[:max_len] + ("..." if len(text) > max_len else "")

    def _field_trace_ref(self, fields, idx):
        if idx is None:
            return None
        for field in fields:
            if field.get("index") == idx:
                return field
        return None

    def _post_submit_ocr_signal(self, screenshot_path):
        try:
            img = cv2.imread(screenshot_path)
            if img is None:
                return {
                    "success": False,
                    "failure": False,
                    "success_matches": [],
                    "failure_matches": [],
                    "excerpt": ""
                }
            h, w = img.shape[:2]
            text, conf = self.ocr.extract_with_context(screenshot_path, (0, 0, w, h), context_margin=0)
            text_blob = text or ""
            success_patterns = [
                ("thank_you", r"\bthank(s)?\s+you\b", 3),
                ("thanks_for_contacting", r"\bthanks?\s+for\s+(contacting|reaching out)\b", 3),
                ("message_sent", r"\bmessage\s+(has\s+been\s+)?(sent|submitted)\b", 3),
                ("form_submitted", r"\bform\s+(has\s+been\s+)?submitted\b", 3),
                ("submission_received", r"\b(submission|request)\s+(has\s+been\s+)?received\b", 3),
                ("we_will_contact", r"\bwe('ll| will)\s+(be in touch|contact you|reach out)\b", 3),
                ("api_json", r"application/json", 2),  # API/test form responses (e.g. httpbin)
                ("api_form_payload", r'"form"\s*:', 2),  # JSON form response
                ("submitted_word", r"\bsubmitted\b", 1),
            ]
            failure_patterns = [
                ("field_error", r"\bone or more fields have an error\b", 4),
                ("required_fields", r"\brequired fields?\b", 2),
                ("field_required", r"\bthis field is required\b", 3),
                ("invalid_input", r"\binvalid\b", 2),
                ("enter_valid_value", r"\bplease\s+enter\s+(an?\s+)?valid\b", 2),
                ("please_choose", r"\bplease\s+choose\b", 1),
                ("please_select", r"\bplease\s+select\b", 1),
                ("check_try_again", r"\bplease check and try again\b", 3),
                ("captcha", r"\b(?:re)?captcha\b", 4),
                ("verification_failed", r"\bverification failed\b", 3),
                ("something_wrong", r"\bsomething went wrong\b", 3),
            ]

            success_matches = []
            success_score = 0
            for label, pattern, weight in success_patterns:
                if re.search(pattern, text_blob, re.IGNORECASE):
                    success_matches.append(label)
                    success_score += weight

            failure_matches = []
            failure_score = 0
            for label, pattern, weight in failure_patterns:
                if re.search(pattern, text_blob, re.IGNORECASE):
                    failure_matches.append(label)
                    failure_score += weight

            success = False
            failure = False
            if success_score >= 3 and failure_score >= 3:
                if failure_score >= success_score:
                    failure = True
                else:
                    success = True
            elif success_score >= 3:
                success = True
            elif failure_score >= 3:
                failure = True

            excerpt = (
                f"conf={conf:.1f} "
                f"s_score={success_score} f_score={failure_score} "
                f"success={success_matches[:3]} "
                f"failure={failure_matches[:3]} "
                f"text='{self._clip(text_blob, 200)}'"
            )
            return {
                "success": success,
                "failure": failure,
                "success_matches": success_matches[:5],
                "failure_matches": failure_matches[:5],
                "excerpt": excerpt
            }
        except Exception as e:
            return {
                "success": False,
                "failure": False,
                "success_matches": [],
                "failure_matches": [],
                "excerpt": f"ocr error: {e}"
            }

    def _write_live_trace(self, trace):
        if not self.live_ocr_enabled:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_host = re.sub(r"[^a-zA-Z0-9]+", "_", trace.get("url", ""))[:80].strip("_") or "url"
        out_path = os.path.join(self.live_ocr_dir, f"{ts}_{safe_host}.json")
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(trace, f, ensure_ascii=False, indent=2)
            logger.info(f"[LIVE OCR] Trace saved: {out_path}")
        except Exception as e:
            logger.warning(f"Failed to write live OCR trace: {e}")

    def _save_annotated_screenshot(self, screenshot_path, field_entries, suffix="annotated"):
        if not self.annotated_screenshots_enabled:
            return ""

        image = cv2.imread(screenshot_path)
        if image is None:
            return ""

        # Draw OCR/classification field overlays.
        for field in field_entries:
            box = field.get("box") or [0, 0, 0, 0]
            x, y, w, h = [int(v) for v in box]
            if w <= 0 or h <= 0:
                continue

            status = field.get("status", "")
            if status == "filled":
                color = (60, 180, 75)  # green
            elif status == "fill_failed":
                color = (0, 0, 255)  # red
            elif status == "ready_to_fill":
                color = (255, 200, 0)  # amber
            else:
                color = (180, 180, 180)  # gray

            cv2.rectangle(image, (x, y), (x + w, y + h), color, 2)

            label = (
                f"OCR idx={field.get('index')} "
                f"{field.get('classified_as')} "
                f"c={field.get('classification_confidence', 0):.0f} "
                f"ocr={field.get('ocr_confidence', 0):.0f} "
                f"src={field.get('source', '')}"
            )
            ty = max(14, y - 6)
            cv2.putText(image, label, (x, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)

        base = os.path.splitext(os.path.basename(screenshot_path))[0]
        out_path = os.path.join(self.annotated_screenshots_dir, f"{base}_{suffix}.png")
        try:
            cv2.imwrite(out_path, image)
            logger.info(f"[ANNOTATE] Saved detection overlay: {out_path}")
            return out_path
        except Exception as e:
            logger.warning(f"[ANNOTATE] Failed to save overlay: {e}")
            return ""

    def _capture_form_preferred_screenshot(self, page, screenshot_path):
        """
        Capture the primary form region when possible; fallback to full-page screenshot.
        Returns metadata with crop origin so OCR/DOM mapping remains aligned.
        """
        form_meta = self._find_primary_form(page)
        if form_meta and form_meta.get("element") is not None:
            try:
                form_meta["element"].screenshot(path=screenshot_path)
                return {
                    "mode": "form",
                    "origin_px": form_meta.get("origin_px", (0, 0)),
                    "form_bbox": form_meta.get("bbox")
                }
            except Exception as e:
                logger.warning(f"Form screenshot failed, falling back to full page: {e}")

        page.screenshot(path=screenshot_path, full_page=True)
        return {
            "mode": "full",
            "origin_px": (0, 0),
            "form_bbox": None
        }

    def _find_primary_form(self, page):
        """
        Find the most likely target form (largest visible form) and compute pixel origin.
        """
        try:
            forms = page.query_selector_all("form")
            if not forms:
                return None

            transforms = page.evaluate("""() => ({
                scrollX: window.scrollX,
                scrollY: window.scrollY,
                devicePixelRatio: window.devicePixelRatio
            })""")
            dpr = transforms.get("devicePixelRatio", 1)
            scroll_x = transforms.get("scrollX", 0)
            scroll_y = transforms.get("scrollY", 0)

            best = None
            for form in forms:
                try:
                    bbox = form.bounding_box()
                    if not bbox:
                        continue
                    w = float(bbox.get("width", 0))
                    h = float(bbox.get("height", 0))
                    if w < 120 or h < 80:
                        continue
                    area = w * h
                    if best is None or area > best["area"]:
                        ox = int((bbox["x"] + scroll_x) * dpr)
                        oy = int((bbox["y"] + scroll_y) * dpr)
                        best = {
                            "area": area,
                            "element": form,
                            "bbox": {
                                "x": float(bbox["x"]),
                                "y": float(bbox["y"]),
                                "width": w,
                                "height": h
                            },
                            "origin_px": (ox, oy)
                        }
                except Exception:
                    continue

            return best
        except Exception as e:
            logger.warning(f"Primary form detection failed: {e}")
            return None

    def _build_monitoring_diagnostics(self, live_trace, result):
        fields = live_trace.get("fields", [])
        status_counts = Counter(f.get("status", "unknown") for f in fields)
        known_classifications = sum(1 for f in fields if f.get("classified_as") not in ("", "unknown"))
        submit = live_trace.get("submit", {})
        fill_attempts = int(result.get("fill_attempts", 0))
        fills_ok = int(result.get("fields_filled", 0))
        ready = int(result.get("elements_ready_to_fill", 0))

        issue = "none"
        if result.get("Submission status") != "success":
            if result.get("captcha_detected"):
                issue = "captcha_obstruction"
            elif ready == 0:
                issue = "ocr_heuristics"
            elif fill_attempts > 0 and fills_ok == 0:
                issue = "form_filler"
            elif submit.get("attempted") and not submit.get("clicked"):
                issue = "submission_click"
            elif submit.get("clicked") and submit.get("ocr_failure"):
                issue = "submission_rejected"
            elif submit.get("clicked") and not (submit.get("dom_success") or submit.get("ocr_success")):
                issue = "submission_confirmation"
            else:
                issue = "mixed_or_unknown"
        elif ready > 0 and fills_ok < ready:
            issue = "partial_fill"

        summary = (
            f"seen={len(fields)} known={known_classifications} ready={ready} "
            f"filled={fills_ok}/{fill_attempts} "
            f"fill_action_failed={result.get('fill_action_failed', 0)} "
            f"fill_verify_failed={result.get('fill_verify_failed', 0)} "
            f"low_conf_skips={result.get('low_confidence_skips', 0)} "
            f"non_fillable_skips={result.get('non_fillable_skips', 0)} "
            f"prefill_miss_skips={result.get('prefill_miss_skips', 0)} "
            f"submit_clicked={int(bool(submit.get('clicked')))} "
            f"submit_success_signal={int(bool(submit.get('dom_success') or submit.get('ocr_success')))} "
            f"submit_failure_signal={int(bool(submit.get('ocr_failure')))}"
        )

        return {
            "monitor_issue": issue,
            "monitor_summary": summary
        }

    def process_batch(self, urls_csv_path="Domains.csv", batch_size=10):
        """Process multiple URLs with rate limiting"""
        try:
            with open(urls_csv_path) as f:
                reader = csv.DictReader(f)
                urls = [row["Website URL"] for row in reader if row.get("Website URL")]
        except Exception as e:
            logger.error(f"Failed to read CSV: {e}")
            return

        total = len(urls)
        logger.info(f"Starting batch processing of {total} URLs")

        for i, url in enumerate(urls):
            if not url.startswith(("http://", "https://")):
                logger.warning(f"Skipping invalid URL: {url}")
                continue

            self.process_url(url)
            logger.info(f"Progress: {i + 1}/{total} URLs processed")

            # Rate limiting
            if i < len(urls) - 1:
                time.sleep(2)

        self.save_results()
        logger.info(f"Batch processing complete. Processed {total} URLs.")

    def save_results(self, output_path=None):
        """Incrementally upsert run results into CSV."""
        try:
            out_path = output_path or self.results_output_path
            if not os.path.isabs(out_path):
                out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), out_path)

            out_dir = os.path.dirname(out_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)

            fieldnames = ["URL", "Submission", "Reason"]
            merged_by_url = {}
            ordered_urls = []

            if os.path.exists(out_path):
                with open(out_path, 'r', newline='', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        url = (row.get("URL") or "").strip()
                        if not url:
                            continue
                        merged_by_url[url] = {
                            "URL": url,
                            "Submission": (
                                row.get("Submission")
                                or row.get("Submission status")
                                or "unsuccessful"
                            ),
                            "Reason": (row.get("Reason") or row.get("reason") or "")
                        }
                        if url not in ordered_urls:
                            ordered_urls.append(url)

            for row in self.results:
                url = (row.get("URL") or "").strip()
                if not url:
                    continue
                exported = {
                    "URL": url,
                    "Submission": (
                        row.get("Submission status")
                        or row.get("Submission")
                        or "unsuccessful"
                    ),
                    "Reason": (row.get("reason") or row.get("Reason") or "")
                }
                if url not in merged_by_url:
                    ordered_urls.append(url)
                merged_by_url[url] = exported

            with open(out_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows([merged_by_url[url] for url in ordered_urls if url in merged_by_url])

            logger.info(f"Results saved incrementally to {out_path} ({len(ordered_urls)} rows)")
        except Exception as e:
            logger.error(f"Failed to save results: {e}")

    def generate_report(self):
        """Generate performance report"""
        if not self.results:
            return {"error": "No results to analyze"}

        successful = [r for r in self.results if r["Submission status"] == "success"]
        captchas = [r for r in self.results if r.get("captcha_detected")]

        total_fields_sum = sum(r.get("total_fields", 0) for r in self.results)

        report = {
            "total_processed": len(self.results),
            "successful_submissions": len(successful),
            "success_rate": (len(successful) / len(self.results)) * 100 if self.results else 0,
            "captcha_encounters": len(captchas),
            "avg_processing_time": sum(r.get("processing_time", 0) for r in self.results) / len(self.results),
            "avg_fields_per_form": sum(r.get("total_fields", 0) for r in self.results) / len(self.results),
            "avg_fill_rate": (sum(r.get("fields_filled", 0) for r in self.results) /
                            total_fields_sum) * 100 if total_fields_sum > 0 else 0,
            "common_errors": {}
        }

        # Count error reasons
        from collections import Counter
        error_reasons = Counter([r["reason"] for r in self.results if r["Submission status"] != "success"])
        report["common_errors"] = dict(error_reasons.most_common(5))

        return report

    def shutdown(self):
        """Clean shutdown"""
        self.browser.close()

# Entry point with error handling
if __name__ == "__main__":
    import sys
    import traceback

    print("=" * 60)
    print("AUTOMATED FORM FILLER - PRODUCTION READY")
    print("=" * 60)

    try:
        filler = AutomatedFormFiller()

        # Check for command line arguments
        if len(sys.argv) > 1:
            # Single URL mode
            url = sys.argv[1]
            print(f"Processing single URL: {url}")
            result = filler.process_url(url)
            print(f"Result: {result['Submission status']} - {result['reason']}")
        else:
            # Batch mode
            print("Starting batch processing from Domains.csv...")
            filler.process_batch("Domains.csv")

        # Generate report
        report = filler.generate_report()
        print("\n" + "=" * 60)
        print("PERFORMANCE REPORT")
        print("=" * 60)
        for key, value in report.items():
            if isinstance(value, float):
                print(f"{key}: {value:.1f}")
            else:
                print(f"{key}: {value}")

        filler.save_results()

    except KeyboardInterrupt:
        print("\n⚠ Process interrupted by user")
        if 'filler' in locals():
            filler.save_results()
    except Exception as e:
        print(f"\n❌ Critical error: {e}")
        traceback.print_exc()
    finally:
        if 'filler' in locals():
            filler.shutdown()
