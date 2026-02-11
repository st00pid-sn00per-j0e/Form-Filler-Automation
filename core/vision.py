"""
UI element detection using computer vision
"""
import cv2
import numpy as np


class UIElementDetector:
    def __init__(self, min_w=40, min_h=18):
        self.min_w = int(min_w)
        self.min_h = int(min_h)

    def detect_form_elements(self, image_path):
        """
        Detect form elements in screenshot
        Returns: List of (x, y, w, h, element_type)
        """
        image = cv2.imread(image_path)
        if image is None:
            return []

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 40, 130)

        # Join broken rectangle borders and masked input outlines.
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if w < self.min_w or h < self.min_h:
                continue
            area = w * h
            if area < 900:
                continue

            aspect = w / float(max(1, h))
            element_type = "input"
            if h >= 70:
                element_type = "textarea"
            elif aspect < 1.8 and h >= 28:
                element_type = "button"

            boxes.append((x, y, w, h, element_type))

        return self._dedupe_boxes(boxes, iou_threshold=0.45)

    def draw_detection_overlay(self, screenshot_path, elements, output_path):
        """Draw detected elements for visual debugging."""
        img = cv2.imread(screenshot_path)
        if img is None:
            return False

        for element in elements:
            box = element.get("box") or (0, 0, 0, 0)
            x, y, w, h = [int(v) for v in box]
            if w <= 0 or h <= 0:
                continue

            if element.get("filled", False):
                color = (0, 255, 0)
            elif element.get("classified_as", "unknown") != "unknown":
                color = (0, 255, 255)
            else:
                color = (0, 0, 255)

            cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
            label = element.get("classified_as", "unknown")
            cv2.putText(
                img,
                label,
                (x, max(12, y - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )

        cv2.imwrite(output_path, img)
        return True

    @staticmethod
    def _dedupe_boxes(boxes, iou_threshold=0.45):
        if not boxes:
            return []
        n = len(boxes)
        rects = np.array([[b[0], b[1], b[0] + b[2], b[1] + b[3]] for b in boxes], dtype=float)
        areas = (rects[:, 2] - rects[:, 0]) * (rects[:, 3] - rects[:, 1])
        idxs = np.argsort(areas)[::-1]
        keep = []

        while len(idxs) > 0:
            i = int(idxs[0])
            keep.append(i)
            if len(idxs) == 1:
                break

            others = idxs[1:]
            xx1 = np.maximum(rects[i, 0], rects[others, 0])
            yy1 = np.maximum(rects[i, 1], rects[others, 1])
            xx2 = np.minimum(rects[i, 2], rects[others, 2])
            yy2 = np.minimum(rects[i, 3], rects[others, 3])
            inter_w = np.maximum(0, xx2 - xx1)
            inter_h = np.maximum(0, yy2 - yy1)
            inter = inter_w * inter_h
            iou = inter / (areas[i] + areas[others] - inter + 1e-6)
            idxs = others[iou < iou_threshold]

        return [boxes[i] for i in keep]
