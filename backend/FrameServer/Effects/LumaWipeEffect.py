import cv2
import numpy as np


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def LumaWipeEffect(img1, img2, duration=0.8, fps=30, mode="dark_to_bright"):
    """
    Reveal img2 over img1 using a luminance threshold.
    mode: 'dark_to_bright' or 'bright_to_dark'
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))

    # Precompute grayscale of img2 once
    gray = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0

    invert = (mode == "bright_to_dark")
    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)
        thr = t  # in [0,1]
        if invert:
            mask = (gray >= (1.0 - thr))
        else:
            mask = (gray <= thr)
        yield np.where(mask[..., None], img2, img1)
