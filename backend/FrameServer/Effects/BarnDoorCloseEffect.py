
def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def BarnDoorCloseEffect(img2, img1, duration=0.8, fps=30):
    """
    Slides left/right halves of img1 over img2. Deterministic and allocation-light.
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))
    max_shift = cols // 2

    left_w = cols // 2
    right_w = cols - left_w
    left_half = img1[:, :left_w]
    right_half = img1[:, left_w:]

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)
        shift = int(round(t * max_shift))

        frame = img2.copy()
        lw = min(shift, left_w)
        if lw > 0:
            frame[:, :lw] = left_half[:, left_w - lw:]
        rw = min(shift, right_w)
        if rw > 0:
            frame[:, cols - rw:] = right_half[:, :rw]
        yield frame
