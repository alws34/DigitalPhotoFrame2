import cv2
import numpy as np


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def SpinZoomFadeEffect(img1, img2, duration=0.8, fps=30, max_deg=12.0, max_scale=1.15):
    """
    Slight rotation+zoom-in of img2 while cross-fading over img1.
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))
    center = (cols * 0.5, rows * 0.5)

    for i in range(steps):
        t = (i + 1) / steps
        te = _ease_smoothstep(t)

        angle = (1.0 - te) * float(max_deg)   # decreases to 0
        scale = 1.0 + (max_scale - 1.0) * te # grows to max_scale

        M = cv2.getRotationMatrix2D(center, angle, scale)
        warped = cv2.warpAffine(img2, M, (cols, rows), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101)

        a = te
        out = (warped.astype(np.float32) * a + img1.astype(np.float32) * (1.0 - a)).astype(np.uint8)
        yield out
