# Effects/CrossZoomEffect.py
import cv2
import numpy as np


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def _resize_center(src, w, h):
    """Resize with center-crop or letterbox to exactly (w,h)."""
    sh, sw = src.shape[:2]
    aspect_src = sw / float(sh)
    aspect_dst = w / float(h)
    if aspect_src > aspect_dst:
        # crop width
        new_w = int(round(sh * aspect_dst))
        x0 = (sw - new_w) // 2
        cropped = src[:, x0:x0 + new_w]
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
    else:
        new_h = int(round(sw / aspect_dst))
        y0 = (sh - new_h) // 2
        cropped = src[y0:y0 + new_h, :]
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)

def CrossZoomEffect(img1, img2, duration=0.8, fps=30, samples=3, max_zoom=1.20):
    """
    Zoom-blur dissolve from img1 to img2 using a few scaled samples of img2.
    samples: number of zoom samples per frame (3 is a good tradeoff)
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))
    samples = max(1, int(samples))

    for i in range(steps):
        t = (i + 1) / steps
        te = _ease_smoothstep(t)

        # Build a small stack of zoomed img2 and average
        acc = np.zeros_like(img2, dtype=np.float32)
        for s in range(samples):
            # weight scales more heavily toward current time
            k = (s + 1) / float(samples)
            z = 1.0 + (max_zoom - 1.0) * te * k
            zw = max(1, int(round(cols * z)))
            zh = max(1, int(round(rows * z)))
            zoomed = cv2.resize(img2, (zw, zh), interpolation=cv2.INTER_LINEAR)
            zoomed = _resize_center(zoomed, cols, rows)
            acc += zoomed.astype(np.float32)

        blurred = (acc / float(samples)).astype(np.uint8)

        # Cross-fade to zoom-blurred img2
        a = te
        out = (blurred.astype(np.float32) * a + img1.astype(np.float32) * (1.0 - a)).astype(np.uint8)
        yield out
