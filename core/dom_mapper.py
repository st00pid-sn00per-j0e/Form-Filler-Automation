"""
Pixel-to-DOM mapping with proper coordinate transformations
"""
import numpy as np
import cv2

class DOMMapper:
    @staticmethod
    def get_element_with_transforms(page, box, viewport_coords=False, screenshot_origin_px=(0, 0)):
        """
        Get DOM element at screen coordinates with proper viewport transforms

        Args:
            page: Playwright page object
            box: (x, y, w, h) - screenshot pixel coords or viewport coords if viewport_coords=True
            viewport_coords: If True, box is already in viewport space (e.g. from DOM bounding_box)
            screenshot_origin_px: top-left pixel offset when screenshot is clipped to a subregion

        Returns: dict with element info or None
        """
        x, y, w, h = box

        if viewport_coords:
            # Box from DOM bounding_box - already viewport-relative
            center_x = x + w / 2
            center_y = y + h / 2
        else:
            # Box from screenshot - apply transforms
            transforms = page.evaluate("""() => {
                return {
                    scrollX: window.scrollX,
                    scrollY: window.scrollY,
                    devicePixelRatio: window.devicePixelRatio
                };
            }""")
            dpr = transforms['devicePixelRatio']
            scroll_x = transforms['scrollX']
            scroll_y = transforms['scrollY']
            origin_x, origin_y = screenshot_origin_px
            # Convert cropped-screenshot pixel coordinates to absolute page pixels first.
            abs_x = x + origin_x
            abs_y = y + origin_y
            viewport_x = (abs_x / dpr) - scroll_x
            viewport_y = (abs_y / dpr) - scroll_y
            center_x = viewport_x + (w / dpr) / 2
            center_y = viewport_y + (h / dpr) / 2

        # Get element, XPath, and attributes in one evaluate (Playwright accepts single arg)
        result = page.evaluate("""({centerX, centerY}) => {
            const el = document.elementFromPoint(centerX, centerY);
            if (!el) return null;

            const getXPath = (node) => {
                if (node.id) return `//*[@id="${node.id}"]`;
                const attrs = ['name', 'data-testid', 'aria-label', 'placeholder'];
                for (const attr of attrs) {
                    if (node.getAttribute(attr)) {
                        return `//${node.tagName.toLowerCase()}[@${attr}="${node.getAttribute(attr)}"]`;
                    }
                }
                const parts = [];
                let current = node;
                while (current && current.nodeType === 1) {
                    let index = 0;
                    let sibling = current.previousSibling;
                    while (sibling) {
                        if (sibling.nodeType === 1 && sibling.tagName === current.tagName) index++;
                        sibling = sibling.previousSibling;
                    }
                    const tagName = current.tagName.toLowerCase();
                    parts.unshift(index ? `${tagName}[${index + 1}]` : tagName);
                    current = current.parentNode;
                }
                return parts.length ? '/' + parts.join('/') : null;
            };

            const attrs = {};
            ['type', 'name', 'id', 'class', 'placeholder', 'aria-label'].forEach(attr => {
                const v = el.getAttribute(attr);
                if (v) attrs[attr] = v;
            });

            // Collect semantic label text for better heuristic classification.
            const textNorm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            let labelText = '';
            try {
                if (el.labels && el.labels.length > 0) {
                    labelText = Array.from(el.labels).map(l => textNorm(l.innerText || l.textContent)).join(' ');
                } else if (el.id) {
                    const l = document.querySelector(`label[for="${el.id}"]`);
                    if (l) labelText = textNorm(l.innerText || l.textContent);
                }
            } catch (e) {}

            let nearbyText = '';
            try {
                const parent = el.closest('label, .form-group, .field, .input-group') || el.parentElement;
                if (parent) nearbyText = textNorm(parent.innerText || parent.textContent).slice(0, 300);
            } catch (e) {}

            attrs['label_text'] = labelText;
            attrs['nearby_text'] = nearbyText;

            return {
                tagName: el.tagName,
                xpath: getXPath(el),
                attributes: attrs
            };
        }""", {"centerX": center_x, "centerY": center_y})

        if not result:
            return None

        return {
            'element': result,
            'xpath': result['xpath'],
            'type': result.get('tagName', 'div').lower(),
            'attributes': result.get('attributes', {}),
            'viewport_coords': (center_x, center_y)
        }

    @staticmethod
    def find_form_elements(page, screenshot_path, boxes, screenshot_origin_px=(0, 0)):
        """
        Map all detected boxes to DOM elements with hybrid detection.
        CV boxes are (x, y, w, h, type) in screenshot coords.
        DOM fallback uses bounding_box viewport coords.
        """
        elements = []
        origin_x, origin_y = screenshot_origin_px

        img = cv2.imread(screenshot_path)
        img_h = img.shape[0] if img is not None else None
        img_w = img.shape[1] if img is not None else None

        transforms = page.evaluate("""() => ({
            scrollX: window.scrollX,
            scrollY: window.scrollY,
            devicePixelRatio: window.devicePixelRatio
        })""")
        dpr = transforms.get('devicePixelRatio', 1)
        scroll_x = transforms.get('scrollX', 0)
        scroll_y = transforms.get('scrollY', 0)

        dom_elements = page.query_selector_all("input, textarea, select, button[type='submit']")
        dom_items = []
        for dom_el in dom_elements:
            try:
                b = dom_el.bounding_box()
                if b:
                    vx, vy, vw, vh = b['x'], b['y'], b['width'], b['height']
                    sx_abs = int((vx + scroll_x) * dpr)
                    sy_abs = int((vy + scroll_y) * dpr)
                    sw = int(vw * dpr)
                    sh = int(vh * dpr)
                    sx = sx_abs - origin_x
                    sy = sy_abs - origin_y

                    # If screenshot is clipped, keep only elements overlapping the image region.
                    if img_w is not None and img_h is not None:
                        if sx + sw <= 0 or sy + sh <= 0 or sx >= img_w or sy >= img_h:
                            continue

                    dom_items.append((sx, sy, sw, sh, vx, vy, vw, vh))
            except Exception:
                continue

        all_detections = []
        for det in boxes:
            if len(det) == 5:
                x, y, w, h, _ = det
                all_detections.append((x, y, w, h, None, None, None, None))
        for sx, sy, sw, sh, vx, vy, vw, vh in dom_items:
            all_detections.append((sx, sy, sw, sh, vx, vy, vw, vh))

        unique = DOMMapper._deduplicate_detections(all_detections)

        for det in unique:
            if len(det) == 8 and det[4] is not None:  # DOM
                sx, sy, sw, sh, vx, vy, vw, vh = det
                dom_info = DOMMapper.get_element_with_transforms(
                    page, (vx, vy, vw, vh), viewport_coords=True
                )
                ocr_box = (sx, sy, sw, sh)
                source = 'dom'
            else:
                x, y, w, h = det[0], det[1], det[2], det[3]
                dom_info = DOMMapper.get_element_with_transforms(
                    page, (x, y, w, h), viewport_coords=False,
                    screenshot_origin_px=screenshot_origin_px
                )
                ocr_box = (x, y, w, h)
                source = 'cv'

            if dom_info:
                elements.append({
                    'box': ocr_box,
                    'detection_source': source,
                    'dom': dom_info,
                    'failed_attempts': 0
                })
        return elements

    @staticmethod
    def _deduplicate_detections(detections, iou_threshold=0.3):
        """Remove overlapping detections. Each det: (x,y,w,h) or (sx,sy,sw,sh,vx,vy,vw,vh)"""
        if not detections:
            return []

        boxes = np.array([[d[0], d[1], d[0] + d[2], d[1] + d[3]] for d in detections])
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])

        # Sort by area (largest first)
        idxs = np.argsort(areas)[::-1]
        keep = []

        while idxs.size > 0:
            i = int(idxs[0])
            keep.append(i)

            # Calculate IoU
            xx1 = np.maximum(boxes[i, 0], boxes[idxs[1:], 0])
            yy1 = np.maximum(boxes[i, 1], boxes[idxs[1:], 1])
            xx2 = np.minimum(boxes[i, 2], boxes[idxs[1:], 2])
            yy2 = np.minimum(boxes[i, 3], boxes[idxs[1:], 3])

            w = np.maximum(0, xx2 - xx1)
            h = np.maximum(0, yy2 - yy1)
            intersection = w * h

            iou = intersection / (areas[i] + areas[idxs[1:]] - intersection + 1e-6)

            # Remove highly overlapping boxes
            idxs = idxs[1:][iou < iou_threshold]

        return [detections[i] for i in keep]
