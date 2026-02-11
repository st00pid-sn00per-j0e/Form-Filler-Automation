"""
Pixel-to-DOM mapping with iframe/shadow-aware DOM harvesting.
"""
import cv2
import numpy as np


class DOMMapper:
    @staticmethod
    def get_element_with_transforms(page, box, viewport_coords=False, screenshot_origin_px=(0, 0)):
        """
        Resolve element at a given point (supports open shadow roots).
        For iframe fields, rely on frame-aware DOM harvesting in find_form_elements().
        """
        x, y, w, h = box

        if viewport_coords:
            center_x = x + w / 2
            center_y = y + h / 2
        else:
            transforms = page.evaluate("""() => ({
                scrollX: window.scrollX,
                scrollY: window.scrollY,
                devicePixelRatio: window.devicePixelRatio || 1
            })""")
            dpr = float(transforms.get("devicePixelRatio", 1) or 1)
            scroll_x = float(transforms.get("scrollX", 0) or 0)
            scroll_y = float(transforms.get("scrollY", 0) or 0)
            origin_x, origin_y = screenshot_origin_px

            abs_x = x + origin_x
            abs_y = y + origin_y
            page_css_x = abs_x / dpr
            page_css_y = abs_y / dpr
            viewport_x = page_css_x - scroll_x
            viewport_y = page_css_y - scroll_y
            center_x = viewport_x + (w / dpr) / 2.0
            center_y = viewport_y + (h / dpr) / 2.0

        result = page.evaluate(
            """({centerX, centerY}) => {
                const cssEscape = (v) => {
                    if (window.CSS && CSS.escape) return CSS.escape(v);
                    return String(v).replace(/["\\\\]/g, "\\\\$&");
                };
                const textNorm = (s) => (s || '').replace(/\\s+/g, ' ').trim();

                const deepElementFromPoint = (root, x, y) => {
                    let el = root.elementFromPoint(x, y);
                    if (!el) return null;
                    while (el && el.shadowRoot) {
                        const inner = el.shadowRoot.elementFromPoint(x, y);
                        if (!inner || inner === el) break;
                        el = inner;
                    }
                    return el;
                };

                const getXPath = (node) => {
                    if (!node) return null;
                    if (node.id) return `//*[@id="${node.id}"]`;
                    const attrs = ['name', 'data-testid', 'aria-label', 'placeholder'];
                    for (const attr of attrs) {
                        const val = node.getAttribute && node.getAttribute(attr);
                        if (val) return `//${node.tagName.toLowerCase()}[@${attr}="${val}"]`;
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

                const buildSelector = (el) => {
                    const tag = (el.tagName || '').toLowerCase();
                    if (!tag) return '';
                    if (el.id) return `#${cssEscape(el.id)}`;
                    const name = el.getAttribute('name');
                    if (name) return `${tag}[name="${name.replace(/"/g, '\\"')}"]`;
                    const aria = el.getAttribute('aria-label');
                    if (aria) return `${tag}[aria-label="${aria.replace(/"/g, '\\"')}"]`;
                    const placeholder = el.getAttribute('placeholder');
                    if (placeholder) return `${tag}[placeholder="${placeholder.replace(/"/g, '\\"')}"]`;
                    return tag;
                };

                const el = deepElementFromPoint(document, centerX, centerY);
                if (!el) return null;

                const attrs = {};
                ['type', 'name', 'id', 'class', 'placeholder', 'aria-label'].forEach(attr => {
                    const v = el.getAttribute && el.getAttribute(attr);
                    if (v) attrs[attr] = v;
                });

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
                    tagName: (el.tagName || '').toLowerCase(),
                    xpath: getXPath(el),
                    selector: buildSelector(el),
                    attributes: attrs
                };
            }""",
            {"centerX": center_x, "centerY": center_y},
        )

        if not result:
            return None

        return {
            "element": result,
            "xpath": result.get("xpath"),
            "selector": result.get("selector", ""),
            "type": result.get("tagName", "div").lower(),
            "attributes": result.get("attributes", {}),
            "viewport_coords": (center_x, center_y),
            "frame_url": page.url,
            "frame_name": "",
            "frame_path": "main",
        }

    @staticmethod
    def find_form_elements(page, screenshot_path, boxes, screenshot_origin_px=(0, 0)):
        """
        Hybrid mapping:
        1) Collect DOM elements from main frame + iframes + open shadow roots.
        2) Merge with CV detections.
        3) Map remaining CV boxes via elementFromPoint fallback.
        """
        elements = []
        origin_x, origin_y = screenshot_origin_px

        img = cv2.imread(screenshot_path)
        img_h = img.shape[0] if img is not None else None
        img_w = img.shape[1] if img is not None else None

        transforms = page.evaluate("""() => ({
            scrollX: window.scrollX,
            scrollY: window.scrollY,
            devicePixelRatio: window.devicePixelRatio || 1
        })""")
        dpr = float(transforms.get("devicePixelRatio", 1) or 1)
        scroll_x = float(transforms.get("scrollX", 0) or 0)
        scroll_y = float(transforms.get("scrollY", 0) or 0)

        dom_items = DOMMapper._collect_dom_items(page, img_w, img_h, dpr, scroll_x, scroll_y, origin_x, origin_y)

        all_detections = []
        for det in boxes:
            if len(det) == 5:
                x, y, w, h, _ = det
                all_detections.append((x, y, w, h, None))
        for item in dom_items:
            all_detections.append((item["sx"], item["sy"], item["sw"], item["sh"], item))

        unique = DOMMapper._deduplicate_detections(all_detections)
        seen_keys = set()

        for det in unique:
            x, y, w, h, item = det
            if item is not None:
                dom_info = {
                    "xpath": item.get("xpath"),
                    "selector": item.get("selector", ""),
                    "type": item.get("dom_type", "div"),
                    "attributes": item.get("attributes", {}),
                    "frame_url": item.get("frame_url", ""),
                    "frame_name": item.get("frame_name", ""),
                    "frame_path": item.get("frame_path", "main"),
                }
                source = item.get("source", "dom")
            else:
                dom_info = DOMMapper.get_element_with_transforms(
                    page,
                    (x, y, w, h),
                    viewport_coords=False,
                    screenshot_origin_px=screenshot_origin_px,
                )
                source = "cv"
                if not dom_info:
                    continue

            key = DOMMapper._element_key(dom_info)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            elements.append({
                "box": (int(x), int(y), int(w), int(h)),
                "detection_source": source,
                "dom": dom_info,
                "failed_attempts": 0,
            })

        return elements

    @staticmethod
    def _collect_dom_items(page, img_w, img_h, dpr, scroll_x, scroll_y, origin_x, origin_y):
        items = []
        frame_offsets = DOMMapper._frame_offsets(page)

        selector = "input, textarea, select, button[type='submit'], [contenteditable='true']"
        for frame in page.frames:
            frame_url = getattr(frame, "url", "") or ""
            frame_name = getattr(frame, "name", "") or ""
            frame_path = DOMMapper._frame_path(frame, page)
            frame_ox, frame_oy = frame_offsets.get(frame, (0.0, 0.0))

            # Standard frame DOM elements.
            try:
                for el in frame.query_selector_all(selector):
                    try:
                        b = el.bounding_box()
                        if not b:
                            continue
                        vx = float(b["x"])
                        vy = float(b["y"])
                        vw = float(b["width"])
                        vh = float(b["height"])
                        if vw < 8 or vh < 8:
                            continue

                        sx_abs = int((vx + scroll_x) * dpr)
                        sy_abs = int((vy + scroll_y) * dpr)
                        sw = int(vw * dpr)
                        sh = int(vh * dpr)
                        sx = sx_abs - origin_x
                        sy = sy_abs - origin_y
                        if not DOMMapper._overlaps_image(sx, sy, sw, sh, img_w, img_h):
                            continue

                        meta = el.evaluate("""(node) => {
                            const textNorm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                            const cssEscape = (v) => {
                                if (window.CSS && CSS.escape) return CSS.escape(v);
                                return String(v).replace(/["\\\\]/g, "\\\\$&");
                            };
                            const attrs = {};
                            ['type','name','id','class','placeholder','aria-label'].forEach(k => {
                                const val = node.getAttribute(k);
                                if (val) attrs[k] = val;
                            });
                            let labelText = '';
                            try {
                                if (node.labels && node.labels.length) {
                                    labelText = Array.from(node.labels).map(l => textNorm(l.innerText || l.textContent)).join(' ');
                                } else if (node.id) {
                                    const l = document.querySelector(`label[for="${node.id}"]`);
                                    if (l) labelText = textNorm(l.innerText || l.textContent);
                                }
                            } catch (e) {}
                            let nearbyText = '';
                            try {
                                const p = node.closest('label, .form-group, .field, .input-group') || node.parentElement;
                                if (p) nearbyText = textNorm(p.innerText || p.textContent).slice(0, 300);
                            } catch (e) {}
                            attrs['label_text'] = labelText;
                            attrs['nearby_text'] = nearbyText;

                            let selector = (node.tagName || '').toLowerCase();
                            if (node.id) selector = `#${cssEscape(node.id)}`;
                            else if (node.getAttribute('name')) {
                                selector = `${(node.tagName || '').toLowerCase()}[name="${node.getAttribute('name').replace(/"/g, '\\"')}"]`;
                            } else if (node.getAttribute('aria-label')) {
                                selector = `${(node.tagName || '').toLowerCase()}[aria-label="${node.getAttribute('aria-label').replace(/"/g, '\\"')}"]`;
                            } else if (node.getAttribute('placeholder')) {
                                selector = `${(node.tagName || '').toLowerCase()}[placeholder="${node.getAttribute('placeholder').replace(/"/g, '\\"')}"]`;
                            }

                            const getXPath = (n) => {
                                if (n.id) return `//*[@id="${n.id}"]`;
                                const parts = [];
                                let cur = n;
                                while (cur && cur.nodeType === 1) {
                                    let index = 0;
                                    let sib = cur.previousSibling;
                                    while (sib) {
                                        if (sib.nodeType === 1 && sib.tagName === cur.tagName) index++;
                                        sib = sib.previousSibling;
                                    }
                                    const tag = cur.tagName.toLowerCase();
                                    parts.unshift(index ? `${tag}[${index + 1}]` : tag);
                                    cur = cur.parentNode;
                                }
                                return parts.length ? '/' + parts.join('/') : null;
                            };

                            return {
                                dom_type: (node.tagName || '').toLowerCase(),
                                attributes: attrs,
                                selector,
                                xpath: getXPath(node)
                            };
                        }""")
                        if not meta:
                            continue
                        items.append({
                            "sx": sx, "sy": sy, "sw": sw, "sh": sh,
                            "dom_type": meta.get("dom_type", "div"),
                            "attributes": meta.get("attributes", {}),
                            "selector": meta.get("selector", ""),
                            "xpath": meta.get("xpath"),
                            "frame_url": frame_url,
                            "frame_name": frame_name,
                            "frame_path": frame_path,
                            "source": "dom",
                        })
                    except Exception:
                        continue
            except Exception:
                pass

            # Open shadow-root fields (same frame).
            try:
                shadow_meta = frame.evaluate("""() => {
                    const textNorm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                    const cssEscape = (v) => {
                        if (window.CSS && CSS.escape) return CSS.escape(v);
                        return String(v).replace(/["\\\\]/g, "\\\\$&");
                    };
                    const roots = [document];
                    const out = [];
                    const seen = new WeakSet();
                    while (roots.length) {
                        const root = roots.pop();
                        const nodes = root.querySelectorAll('*');
                        for (const n of nodes) {
                            if (n.shadowRoot) roots.push(n.shadowRoot);
                            if (seen.has(n)) continue;
                            seen.add(n);
                            const tag = (n.tagName || '').toLowerCase();
                            const type = (n.getAttribute && n.getAttribute('type') || '').toLowerCase();
                            const isField =
                                tag === 'input' || tag === 'textarea' || tag === 'select' ||
                                (tag === 'button' && type === 'submit') ||
                                n.getAttribute('contenteditable') === 'true';
                            if (!isField) continue;
                            const rect = n.getBoundingClientRect();
                            if (!rect || rect.width < 8 || rect.height < 8) continue;
                            const attrs = {};
                            ['type','name','id','class','placeholder','aria-label'].forEach(k => {
                                const val = n.getAttribute(k);
                                if (val) attrs[k] = val;
                            });
                            attrs['label_text'] = '';
                            attrs['nearby_text'] = textNorm((n.parentElement && n.parentElement.textContent) || '').slice(0, 300);
                            let selector = tag;
                            if (n.id) selector = `#${cssEscape(n.id)}`;
                            else if (n.getAttribute('name')) selector = `${tag}[name="${n.getAttribute('name').replace(/"/g, '\\"')}"]`;
                            else if (n.getAttribute('aria-label')) selector = `${tag}[aria-label="${n.getAttribute('aria-label').replace(/"/g, '\\"')}"]`;
                            else if (n.getAttribute('placeholder')) selector = `${tag}[placeholder="${n.getAttribute('placeholder').replace(/"/g, '\\"')}"]`;
                            out.push({
                                x: rect.x, y: rect.y, w: rect.width, h: rect.height,
                                dom_type: tag,
                                attributes: attrs,
                                selector: selector,
                            });
                        }
                    }
                    return out;
                }""")
                for m in shadow_meta or []:
                    vx = float(m.get("x", 0)) + frame_ox
                    vy = float(m.get("y", 0)) + frame_oy
                    vw = float(m.get("w", 0))
                    vh = float(m.get("h", 0))
                    if vw < 8 or vh < 8:
                        continue
                    sx_abs = int((vx + scroll_x) * dpr)
                    sy_abs = int((vy + scroll_y) * dpr)
                    sw = int(vw * dpr)
                    sh = int(vh * dpr)
                    sx = sx_abs - origin_x
                    sy = sy_abs - origin_y
                    if not DOMMapper._overlaps_image(sx, sy, sw, sh, img_w, img_h):
                        continue
                    items.append({
                        "sx": sx, "sy": sy, "sw": sw, "sh": sh,
                        "dom_type": m.get("dom_type", "div"),
                        "attributes": m.get("attributes", {}),
                        "selector": m.get("selector", ""),
                        "xpath": None,
                        "frame_url": frame_url,
                        "frame_name": frame_name,
                        "frame_path": frame_path,
                        "source": "shadow_dom",
                    })
            except Exception:
                pass

        # Deduplicate collected DOM candidates by selector/frame and IoU.
        dedup = []
        seen = set()
        for item in items:
            key = (
                item.get("frame_url", ""),
                item.get("frame_name", ""),
                item.get("selector", ""),
                item.get("xpath") or "",
            )
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        return dedup

    @staticmethod
    def _frame_offsets(page):
        offsets = {}
        for frame in page.frames:
            if frame == page.main_frame:
                offsets[frame] = (0.0, 0.0)
                continue
            try:
                el = frame.frame_element()
                b = el.bounding_box()
                if b:
                    offsets[frame] = (float(b.get("x", 0)), float(b.get("y", 0)))
                else:
                    offsets[frame] = (0.0, 0.0)
            except Exception:
                offsets[frame] = (0.0, 0.0)
        return offsets

    @staticmethod
    def _frame_path(frame, page):
        if frame == page.main_frame:
            return "main"
        parent = frame.parent_frame
        chain = []
        cur = frame
        while cur and cur != page.main_frame:
            label = cur.name or "iframe"
            chain.append(label)
            cur = cur.parent_frame
        chain.reverse()
        return "main/" + "/".join(chain)

    @staticmethod
    def _overlaps_image(sx, sy, sw, sh, img_w, img_h):
        if img_w is None or img_h is None:
            return True
        if sx + sw <= 0 or sy + sh <= 0 or sx >= img_w or sy >= img_h:
            return False
        return True

    @staticmethod
    def _element_key(dom_info):
        return (
            dom_info.get("frame_url", ""),
            dom_info.get("frame_name", ""),
            dom_info.get("selector", ""),
            dom_info.get("xpath") or "",
            dom_info.get("type", ""),
        )

    @staticmethod
    def _deduplicate_detections(detections, iou_threshold=0.3):
        """Each detection is (x,y,w,h,payload)."""
        if not detections:
            return []

        boxes = np.array([[d[0], d[1], d[0] + d[2], d[1] + d[3]] for d in detections], dtype=float)
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        idxs = np.argsort(areas)[::-1]
        keep = []

        while idxs.size > 0:
            i = int(idxs[0])
            keep.append(i)
            if idxs.size == 1:
                break

            xx1 = np.maximum(boxes[i, 0], boxes[idxs[1:], 0])
            yy1 = np.maximum(boxes[i, 1], boxes[idxs[1:], 1])
            xx2 = np.minimum(boxes[i, 2], boxes[idxs[1:], 2])
            yy2 = np.minimum(boxes[i, 3], boxes[idxs[1:], 3])

            w = np.maximum(0, xx2 - xx1)
            h = np.maximum(0, yy2 - yy1)
            intersection = w * h
            iou = intersection / (areas[i] + areas[idxs[1:]] - intersection + 1e-6)
            idxs = idxs[1:][iou < iou_threshold]

        return [detections[i] for i in keep]
