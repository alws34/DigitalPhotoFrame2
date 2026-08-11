
def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def BarnDoorOpenEffect(img1, img2, duration=0.8, fps=30):
    """
    img1 halves slide outward revealing img2.
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))
    left_w = cols // 2
    right_w = cols - left_w
    left_half = img1[:, :left_w]
    right_half = img1[:, left_w:]

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)
        shift = int(round(t * (cols // 2)))

        frame = img2.copy()

        # Left visible width shrinks as it slides left
        lvw = max(0, left_w - shift)
        if lvw > 0:
            frame[:, :lvw] = left_half[:, shift:shift + lvw]

        # Right visible width shrinks as it slides right
        rvw = max(0, right_w - shift)
        if rvw > 0:
            frame[:, cols - rvw:] = right_half[:, :rvw]
        yield frame
