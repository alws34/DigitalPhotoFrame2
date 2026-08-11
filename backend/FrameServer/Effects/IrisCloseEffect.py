import numpy as np


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def IrisCloseEffect(img2, img1, duration=0.8, fps=30):
    """
    Circular hide to center. Vectorized; no cv2.circle per frame.
    """
    rows, cols, _ = img2.shape
    steps = max(1, int(round(duration * fps)))
    yy, xx = np.ogrid[:rows, :cols]
    cx, cy = cols // 2, rows // 2
    dist2 = (xx - cx) ** 2 + (yy - cy) ** 2
    max_r2 = dist2.max()

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)
        r2 = int(round((1.0 - t) * (1.0 - t) * max_r2))
        mask = dist2 > r2
        yield np.where(mask[..., None], img1, img2)
