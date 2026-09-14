"""MVLSB packing (Phase 2). Turns a boolean pixel array into the page-major,
LSB-top byte layout the SSD1305 (and adafruit_framebuf) expect — the same
layout oled_hud.driver.PartialSSD1305.blit() writes to GDDRAM.
"""

import numpy as np


def pack_bits(bits: np.ndarray) -> np.ndarray:
    """(h, w) bool array, h % 8 == 0 -> (h//8, w) uint8, page-major MVLSB."""
    h, w = bits.shape
    if h % 8:
        raise ValueError(f"height {h} is not a multiple of 8")
    return np.packbits(bits.reshape(h // 8, 8, w), axis=1, bitorder="little").reshape(h // 8, w)


def pack_image(img) -> np.ndarray:
    """PIL image (any mode) -> packed MVLSB. Converts to 1-bit without dithering
    for already-black/white sources (e.g. ImageDraw text); feeding it a photo
    or other grayscale/color image will dither via convert("1")'s default.
    """
    return pack_bits(np.asarray(img.convert("1"), dtype=np.uint8) > 0)
