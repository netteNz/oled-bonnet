"""Golden-reference MVLSB packer.

`image_to_pages` below is lifted unchanged from walk.py, where it was
confirmed correct against real SSD1305 hardware (clean stick-figure sprite
render, no shifted/garbled rows). Do not "clean it up" — its value as a test
oracle for oled_hud.pack.pack_bits comes from being the exact code that was
validated on the panel.
"""

from PIL import Image


def image_to_pages(img: Image.Image) -> bytes:
    """Pack a mode-'1' image into page-major bytes matching blit()'s layout."""
    w, h = img.size
    npages = h // 8
    px = img.load()
    data = bytearray(w * npages)
    for page in range(npages):
        base = page * 8
        for col in range(w):
            byte = 0
            for bit in range(8):
                if px[col, base + bit]:
                    byte |= 1 << bit
            data[page * w + col] = byte
    return bytes(data)


def reference_pack(bits) -> bytes:
    """(h, w) bool array -> page-major MVLSB bytes, via the golden packer."""
    h, w = bits.shape
    img = Image.fromarray((bits.astype("uint8") * 255), mode="L").convert("1")
    return image_to_pages(img)
