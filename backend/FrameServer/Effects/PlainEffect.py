
def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def PlainEffect(img1, img2, duration=0.8, fps=30):
    """
    No-op transition: hold img1 for duration at fps.
    """
    steps = max(1, int(round(duration * fps)))
    for _ in range(steps):
        # Copy to avoid aliasing if downstream mutates frames in-place
        yield img1.copy()
