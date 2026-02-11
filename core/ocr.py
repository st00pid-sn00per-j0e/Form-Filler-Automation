"""
Text extraction with confidence scoring
"""
import pytesseract
import cv2
import numpy as np
from pytesseract import Output

class TextExtractor:
    def __init__(self, lang='eng', min_confidence=50):
        self.lang = lang
        self.min_confidence = min_confidence

    def extract_with_context(self, image_path, target_box, context_margin=50):
        """
        Extract text with confidence filtering
        Returns: (text, confidence_score)
        """
        img = cv2.imread(image_path)
        if img is None:
            return "", 0

        # Ensure integer indices (DOM bounding_box returns floats)
        x, y, w, h = (int(v) for v in target_box)

        # Expand area for context
        x1 = max(0, x - context_margin)
        y1 = max(0, y - context_margin)
        x2 = min(img.shape[1], x + w + context_margin)
        y2 = min(img.shape[0], y + h + context_margin)

        roi = img[y1:y2, x1:x2]
        processed = self._preprocess_image(roi)

        # Get OCR data with confidence
        data = pytesseract.image_to_data(
            processed,
            config=f"--psm 6 -l {self.lang}",
            output_type=Output.DICT
        )

        # Filter by confidence
        texts = []
        confidences = []

        for i in range(len(data['text'])):
            conf = int(data['conf'][i])
            text = data['text'][i].strip()

            if conf > self.min_confidence and text:
                texts.append(text)
                confidences.append(conf)

        if not texts:
            return "", 0

        # Weight texts by confidence
        weighted_text = ' '.join(texts)
        avg_confidence = sum(confidences) / len(confidences)

        return weighted_text.lower(), avg_confidence

    def verify_fill(self, image_path, box, expected_value, min_match_threshold=0.7):
        """
        Verify field fill using fuzzy matching
        """
        extracted, confidence = self.extract_with_context(image_path, box)

        if not extracted or confidence < 30:
            return False

        # Simple fuzzy matching
        expected_lower = expected_value.lower()
        extracted_lower = extracted.lower()

        # Check if expected is in extracted or vice versa
        if expected_lower in extracted_lower or extracted_lower in expected_lower:
            return True

        # Calculate character overlap
        expected_chars = set(expected_lower.replace(' ', ''))
        extracted_chars = set(extracted_lower.replace(' ', ''))

        if expected_chars and extracted_chars:
            overlap = len(expected_chars.intersection(extracted_chars)) / len(expected_chars)
            return overlap >= min_match_threshold

        return False

    def _preprocess_image(self, image):
        """Preprocess image for better OCR"""
        # Convert to grayscale
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # Apply threshold to get binary image
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Noise removal
        kernel = np.ones((1, 1), np.uint8)
        opening = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)

        return opening
