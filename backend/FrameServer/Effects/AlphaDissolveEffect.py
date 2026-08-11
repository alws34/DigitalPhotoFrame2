import cv2


def _ease_smoothstep(t: float) -> float:
    # Smoothstep easing for nicer motion; keep linear by returning t if you prefer
    return t * t * (3.0 - 2.0 * t)

def AlphaDissolveEffect(img1, img2, duration=0.8, fps=30):
    """
    Deterministic, fps-driven dissolve. Yields exactly duration*fps frames.
    """
    steps = max(1, int(round(duration * fps)))
    for i in range(steps):
        t = (i + 1) / steps
        a = _ease_smoothstep(t)
        yield cv2.addWeighted(img1, 1.0 - a, img2, a, 0.0)
