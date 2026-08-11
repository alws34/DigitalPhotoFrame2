import cv2


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def ZoomInEffect(img2, img1, duration=0.8, fps=30):
    """
    Center box of img2 shrinks into img1.
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)
        scale = max(0.0, 1.0 - t)  # from 1.0 -> 0.0

        cur_w = max(1, int(round(cols * scale)))
        cur_h = max(1, int(round(rows * scale)))
        x0 = (cols - cur_w) // 2
        y0 = (rows - cur_h) // 2

        frame = img1.copy()
        if cur_w > 0 and cur_h > 0:
            patch = cv2.resize(img2, (cur_w, cur_h), interpolation=cv2.INTER_LINEAR)
            frame[y0:y0 + cur_h, x0:x0 + cur_w] = patch
        yield frame
