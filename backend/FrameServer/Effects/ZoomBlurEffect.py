import cv2
import numpy as np


def _ease(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def _to_3ch_uint8(img):
    if img is None:
        raise ValueError("img is None")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    if img.dtype != np.uint8:
        img = img.astype(np.uint8)
    return np.ascontiguousarray(img)

def _center_crop(src, w, h):
    sh, sw = src.shape[:2]
    if sw == w and sh == h:
        return src
    x0 = max(0, (sw - w) // 2)
    y0 = max(0, (sh - h) // 2)
    return src[y0:y0+h, x0:x0+w]

def ZoomBlurEffect(
    img1,
    img2,
    duration=0.8,
    fps=30,
    max_scale=1.08,     # max zoom amount applied to img1 during the transition
    samples=6           # number of zoom samples per frame (keep small)
):
    """
    Crossfade while applying a subtle zoom blur to img1.
    No remap; only a few resizes + accumulates per frame.
    """
    img1 = _to_3ch_uint8(img1)
    img2 = _to_3ch_uint8(img2)
    if img2.shape[:2] != img1.shape[:2]:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]), interpolation=cv2.INTER_LINEAR)

    h, w = img1.shape[:2]
    img2f = np.ascontiguousarray(img2.astype(np.float32))
    steps = max(1, int(round(duration * fps)))

    for i in range(steps):
        t = (i + 1) / steps
        te = _ease(t)

        # Build zoom blur of img1
        s_min = 1.0
        s_max = 1.0 + (max_scale - 1.0) * te
        acc = np.zeros((h, w, 3), dtype=np.float32)
        for j in range(samples):
            s = s_min + (s_max - s_min) * (j / max(1, samples - 1))
            zw = max(1, int(round(w * s)))
            zh = max(1, int(round(h * s)))
            z = cv2.resize(img1, (zw, zh), interpolation=cv2.INTER_LINEAR)
            zc = _center_crop(z, w, h).astype(np.float32)
            acc += zc
        blur1 = acc / float(samples)

        # Crossfade to img2
        a = np.float32(te)
        out = (img2f * a + blur1 * (1.0 - a)).astype(np.uint8)
        yield out
