import cv2
import numpy as np


def _ease_smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)

def PixelDissolveEffect(img1, img2, duration=0.8, block_size=10, fps=30, seed=None):
    """
    Deterministic pixel dissolve using block permutation. No per-frame RNG or kron.
    """
    rows, cols, _ = img1.shape
    steps = max(1, int(round(duration * fps)))

    hb = max(1, rows // max(1, block_size))
    wb = max(1, cols // max(1, block_size))
    nblocks = hb * wb

    rng = np.random.default_rng(seed)
    order = rng.permutation(nblocks)  # 0..nblocks-1
    order_map = order.reshape(hb, wb)

    for i in range(steps):
        t = _ease_smoothstep((i + 1) / steps)
        k = int(round(t * nblocks))
        # Blocks with rank < k are "on"
        rank_mask_small = (order_map < k).astype(np.uint8) * 255
        mask_up = cv2.resize(rank_mask_small, (cols, rows), interpolation=cv2.INTER_NEAREST)
        mask = mask_up.astype(bool)
        yield np.where(mask[..., None], img2, img1)
