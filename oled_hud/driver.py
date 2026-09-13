"""Partial-blit driver for the SSD1305 OLED.

Extends Adafruit's SSD1305_I2C with `blit()`, which pushes only a
page/column rectangle instead of the full frame, so animations that
touch a small region don't pay for a full 512-byte I2C transaction.
Addressing mirrors `_SSD1305.show()` in `adafruit_ssd1305` — the
controller is in horizontal addressing mode, so bytes written after
SET_COL_ADDR/SET_PAGE_ADDR auto-increment across columns then pages,
which is what lets a sub-rectangle write land in the right place.
"""

from adafruit_ssd1305 import SET_COL_ADDR, SET_PAGE_ADDR, SSD1305_I2C


class PartialSSD1305(SSD1305_I2C):
    """SSD1305 I2C driver with a partial-region blit."""

    def blit(self, data: bytes, *, col: int, ncols: int, page0: int, page1: int) -> None:
        """Write `data` to the col..col+ncols-1 / page0..page1 rectangle.

        `data` must be page-major: page0's ncols bytes, then page1's,
        and so on, matching the GDDRAM layout `show()` relies on.
        """
        npages = page1 - page0 + 1
        expected = ncols * npages
        if len(data) != expected:
            raise ValueError(
                f"expected {expected} bytes for {npages} page(s) x {ncols} cols, "
                f"got {len(data)}"
            )

        xpos0 = col
        xpos1 = col + ncols - 1
        if self.width == 64:
            # displays with width of 64 pixels are shifted by 32, same as show()
            xpos0 += 32
            xpos1 += 32

        self.write_cmd(SET_COL_ADDR)
        self.write_cmd(xpos0 + self._column_offset)
        self.write_cmd(xpos1 + self._column_offset)
        self.write_cmd(SET_PAGE_ADDR)
        self.write_cmd(page0)
        self.write_cmd(page1)

        for p in range(npages):
            page = page0 + p
            start = page * self.width + col
            self.buffer[1 + start : 1 + start + ncols] = data[p * ncols : (p + 1) * ncols]

        payload = bytearray(1 + expected)
        payload[0] = 0x40  # Co=0, D/C=1, same prefix write_framebuf() uses
        payload[1:] = data
        with self.i2c_device:
            self.i2c_device.write(payload)
