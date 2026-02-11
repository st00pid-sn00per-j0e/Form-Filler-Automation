"""
Field classification with captcha handling.
Pipeline: keywords -> HTML attrs -> fuzzy -> MiniLM (semantic) -> fuzzy fallback -> log unknown.
"""
import logging
import os
import re
from datetime import datetime
from rapidfuzz import fuzz

from core.semantic_classifier import classify_semantic

logger = logging.getLogger(__name__)

UNKNOWN_PATTERNS_LOG = os.path.join("logs", "unknown_patterns.jsonl")
os.makedirs("logs", exist_ok=True)


def _log_unknown_pattern(combined_text, attributes, element_type, enabled=True):
    """Append unknown pattern for iterative improvement."""
    if not enabled:
        return
    try:
        entry = {
            "ts": datetime.now().isoformat(),
            "combined_text": (combined_text or "")[:500],
            "attributes": {str(k): str(v)[:200] for k, v in (attributes or {}).items()},
            "element_type": element_type,
        }
        import json
        with open(UNKNOWN_PATTERNS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.debug(f"[UNKNOWN] Logged pattern: {combined_text[:80]}...")
    except Exception as e:
        logger.warning(f"Failed to log unknown pattern: {e}")


class FieldClassifier:
    FIELD_PATTERNS = {
        "first_name": ["first name", "firstname", "given name", "your first name", "fname", "f_name"],
        "last_name": ["last name", "lastname", "surname", "family name", "your last name", "lname", "l_name"],
        "name": [
            "name", "full name", "your name", "customer name", "contact name",
            "your full name", "fullname", "contact person", "applicant name"
        ],
        "email": [
            "email", "e-mail", "mail address", "your email", "email address",
            "work email", "business email", "your e-mail"
        ],
        "phone": [
            "phone", "mobile", "telephone", "phone number", "contact number",
            "tel", "cell", "your phone", "work phone", "mobile number"
        ],
        "company": [
            "company", "organization", "business", "employer", "company name",
            "organization name", "business name", "your company", "company/organization"
        ],
        "subject": [
            "subject", "title", "regarding", "reason for contact", "topic",
            "inquiry type", "reason", "nature of inquiry", "how can we help"
        ],
        "message": [
            "message", "comment", "inquiry", "details", "your message",
            "comments", "additional info", "additional information", "description",
            "project details", "tell us more", "how can we help you"
        ],
        "captcha": ["captcha", "security code", "verification", "i'm not a robot",
                   "prove you are human", "anti-spam"],
        "dropdown": ["select", "choose", "option", "country", "state", "city"],
        "choice": ["checkbox", "radio", "option", "terms", "privacy", "consent"]
    }

    @staticmethod
    def classify(text, element_type, attributes, min_confidence=70, use_minilm=True, log_unknown=True):
        """
        Multi-factor classification with fuzzy matching
        Returns: (field_type, confidence)
        """
        text_lower = (text or "").lower()
        attrs = {str(k).lower(): str(v).lower() for k, v in (attributes or {}).items() if v}
        attr_blob_raw = " ".join([
            attrs.get("name", ""),
            attrs.get("id", ""),
            attrs.get("placeholder", ""),
            attrs.get("aria-label", ""),
            attrs.get("label_text", ""),
            attrs.get("nearby_text", ""),
            attrs.get("class", "")
        ]).strip()
        attr_blob = re.sub(r"[_\-\./]+", " ", attr_blob_raw)
        combined_text = f"{attr_blob} {text_lower}".strip()

        # Hard-stop non-fillable control types before any fuzzy matching.
        if element_type in ["button"]:
            return "submit", 98

        # 1. Check for captcha first (hard stop)
        for pattern in FieldClassifier.FIELD_PATTERNS["captcha"]:
            if pattern in combined_text:
                return "captcha", 100

        # 2. HTML input type analysis (highest confidence)
        attr_type = attrs.get("type", "")
        if attr_type:
            if attr_type == 'email':
                return "email", 95
            elif attr_type in ['tel', 'phone']:
                return "phone", 95
            elif attr_type in ['submit', 'button', 'reset']:
                return "submit", 98
            elif attr_type in ['file', 'image']:
                return "file", 98
            elif attr_type in ['search']:
                return "subject", 70
            elif attr_type in ['checkbox', 'radio']:
                return "choice", 95

        # 2a. Native textarea is almost always free-form message/details.
        if element_type == 'textarea':
            return "message", 95

        # 2b. Native select should stay select/dropdown and not be hijacked
        # by option text (e.g., "Email" in a "How did you hear" menu).
        if element_type == 'select':
            return "dropdown", 95

        # 3. Attribute keyword matching (very reliable for forms)
        if ("full name" in attr_blob) or ("your name" in attr_blob and "first name" not in attr_blob):
            return "name", 92

        for field, patterns in FieldClassifier.FIELD_PATTERNS.items():
            for pattern in patterns:
                if pattern in attr_blob:
                    return field, 90

        # 4. Fuzzy matching on combined OCR + attributes (lower threshold for attribute-heavy text)
        attr_weight = 1 if attr_blob_raw.strip() else 0
        effective_min = min(60, min_confidence) if attr_weight else min_confidence
        if combined_text and attr_type not in ['checkbox', 'radio']:
            best_match = None
            best_score = 0

            for field, patterns in FieldClassifier.FIELD_PATTERNS.items():
                if field in ("captcha", "choice"):
                    continue
                for pattern in patterns:
                    score = max(
                        fuzz.partial_ratio(pattern, combined_text),
                        fuzz.token_set_ratio(pattern, combined_text)
                    )
                    if score > best_score and score >= effective_min:
                        best_score = score
                        best_match = field

            if best_match:
                return best_match, best_score

        # 4b. Attribute-only fallback: split name/id/placeholder by _ - to catch customer_name, etc.
        tokens = set(re.split(r"[_\-\s\.]+", attr_blob_raw.lower()))
        if "name" in tokens and not {"first", "last", "surname", "given", "family"}.intersection(tokens):
            return "name", 75
        if "email" in tokens or "mail" in tokens:
            return "email", 80
        if "phone" in tokens or "tel" in tokens or "mobile" in tokens:
            return "phone", 80
        if "company" in tokens or "organization" in tokens or "business" in tokens:
            return "company", 80
        if "message" in tokens or "comment" in tokens or "inquiry" in tokens or "details" in tokens:
            return "message", 75

        # 5. MiniLM semantic classification (before returning unknown)
        if use_minilm and combined_text and attr_type not in ('checkbox', 'radio'):
            field_type_sem, conf_sem = classify_semantic(combined_text, min_similarity=0.45)
            if field_type_sem:
                return field_type_sem, conf_sem

        # 6. Fuzzy fallback with lower threshold (edge cases)
        FUZZY_FALLBACK_MIN = 50
        if combined_text and attr_type not in ('checkbox', 'radio'):
            best_match = None
            best_score = 0
            for field, patterns in FieldClassifier.FIELD_PATTERNS.items():
                if field in ("captcha", "choice"):
                    continue
                for pattern in patterns:
                    score = max(
                        fuzz.partial_ratio(pattern, combined_text),
                        fuzz.token_set_ratio(pattern, combined_text),
                    )
                    if score > best_score and score >= FUZZY_FALLBACK_MIN:
                        best_score = score
                        best_match = field
            if best_match:
                return best_match, best_score

        # 7. Element type heuristics
        if element_type == 'textarea':
            return "message", 60
        elif element_type == 'select':
            return "dropdown", 70

        # 8. Log unknown for iterative improvement
        _log_unknown_pattern(combined_text, attributes, element_type, enabled=log_unknown)
        return "unknown", 0

    @staticmethod
    def should_skip_field(field_type, element_info):
        """
        Determine if field should be skipped
        """
        skip_types = ['captcha', 'button', 'submit', 'file']
        skip_attr_types = ['checkbox', 'radio']

        if field_type in skip_types:
            return True

        attr_type = str(element_info['dom'].get('attributes', {}).get('type', '')).lower()
        dom_type = str(element_info.get('dom', {}).get('type', '')).lower()
        if field_type == 'choice' or attr_type in skip_attr_types:
            return True

        # Ignore non-input-like tags captured by visual detection overlays.
        if dom_type not in ['input', 'textarea', 'select']:
            return True

        # Skip already failed elements
        if element_info.get('failed_attempts', 0) >= 2:
            return True

        # Skip hidden fields
        if element_info['dom'].get('attributes', {}).get('type') == 'hidden':
            return True

        return False
