"""Packed-tape core (Phase 2). A Tape rasterizes once at construction and
hands out ready-to-blit MVLSB frames from then on — frame() never touches
PIL, so it's safe to call from a tight animation loop.
"""

import numpy as np

from oled_hud.pack import pack_bits

VIEW = 128


class Tape:
    def __init__(self, img, gap: int = 24):
        bits = np.asarray(img.convert("1"), dtype=np.uint8) > 0
        h, w = bits.shape
        loop = np.concatenate([bits, np.zeros((h, gap), dtype=bool)], axis=1)
        self.period = loop.shape[1]
        if self.period < VIEW:
            raise ValueError(
                f"tape period {self.period}px (image width {w} + gap {gap}) "
                f"is shorter than the {VIEW}px viewport"
            )
        self.packed = pack_bits(np.concatenate([loop, loop[:, :VIEW]], axis=1))
        self.pages = h // 8

    def frame(self, x: int) -> bytes:
        i = x % self.period
        return self.packed[:, i:i + VIEW].tobytes()
