import numpy as np


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def IrisOpenEffect(img1, img2, duration=0.8, fps=30):
    """
    Circular reveal from center. Uses a precomputed distance map; no cv2.circle per frame.
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))
    yy, xx = np.ogrid[:rows, :cols]
    cx, cy = cols // 2, rows // 2
    dist2 = (xx - cx) ** 2 + (yy - cy) ** 2
    max_r2 = dist2.max()

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)
        r2 = int(round(t * t * max_r2))  # quadratic growth feels better
        mask = dist2 <= r2
        yield np.where(mask[..., None], img2, img1)
