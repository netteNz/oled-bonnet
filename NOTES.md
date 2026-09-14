# Driver notes (Phase 1)

Extracted from the installed `adafruit_ssd1305` source (v1.4.6):

```
.env/lib/python3.13/site-packages/adafruit_ssd1305.py
```

## Addressing mode

`init_display()` sets `SET_MEM_ADDR` (0x20) to `0x00` — horizontal addressing
mode. Writes after `SET_COL_ADDR`/`SET_PAGE_ADDR` auto-increment across
columns first, then wrap to the next page. This is what lets a sub-rectangle
write in `blit()` land in the right place without a full-buffer resend.

## Column offset

`show()` sends:

```python
xpos0 = 0
xpos1 = self.width - 1
if self.width == 64:
    xpos0 += 32
    xpos1 += 32
self.write_cmd(SET_COL_ADDR)
self.write_cmd(xpos0 + self._column_offset)
self.write_cmd(xpos1 + self._column_offset)
```

`self._column_offset` defaults to **4** (`_SSD1305.__init__`, overridable via
the `col=` kwarg, which nothing in this repo passes). So for our 128x32
panel the real GDDRAM column window is **4..131**, not 0..127 — confirms the
handoff's warning not to assume the SSD1306's offset of 0. `driver.py`'s
`blit()` reads `self._column_offset` directly from the instance rather than
hardcoding 4, so it stays correct if that default ever changes.

The `width == 64` shift (+32) doesn't apply to our 128-wide panel but
`blit()` mirrors it anyway for consistency with `show()`.

## Page range

Page addressing is always `0..(height // 8 - 1)` — 4 pages (0-3) for our
32px-tall panel. `blit()` takes `page0`/`page1` explicitly rather than
hardcoding the full range, but since addressing is horizontal-mode and
GDDRAM can't be read back over I2C, a blit is only ever safe across whole
pages (no partial-row read-modify-write is possible).

## `write_cmd`

Public (no leading underscore) on `SSD1305_I2C`, implemented as a 2-byte
I2C write (`0x80`, `cmd`) per call. `driver.py`'s `blit()` calls it directly
for `SET_COL_ADDR`/`SET_PAGE_ADDR`.

Phase 4 took this escape hatch up as predicted: `oled_hud/effects.py` uses
it for `all_on()` (`SET_ENTIRE_ON`, 0xA4/0xA5), which the Adafruit class
doesn't expose — `contrast()` and `invert()` it does expose, so `effects.py`
calls those rather than re-deriving the command bytes. The 2-byte-per-call
cost is what makes these safe inside a frame loop: a command effect is a
couple of bytes against a 256-byte blit, so it rides along in the frame
budget instead of competing with the ticker for bandwidth.

## I2C buffer framing

`SSD1305_I2C.buffer` is `pages * width + 1` bytes, with `buffer[0] = 0x40`
(Co=0, D/C=1) baked in permanently; the framebuffer view passed to
`framebuf.FrameBuffer` is offset by 1 to hide that prefix byte. `blit()`
follows the same convention: it prefixes its own payload with `0x40` before
the raw pixel bytes.

## Open items (not yet done)

- **`hw_scroll_spike.py`** — the one-off hardware-scroll spike (0x2E
  deactivate, fill buffer, 0x26 setup + 0x2F activate, confirm wraparound)
  called for in the handoff has not been run. It never blocked anything:
  the tape approach explicitly avoids hardware scroll, and all five phases
  shipped without it.

  Two things from the source worth knowing before attempting it. The
  library has **no** scroll support — 0x26/0x27/0x2F appear nowhere in
  `adafruit_ssd1305.py` — so a spike has to drive them through `write_cmd`
  directly. And `init_display()` already emits a bare `0x2E` (deactivate
  scroll), though it does so by accident: it is written as
  `SET_DISP_START_LINE | 0x00` followed by `0x2E` commented "SET_DISP_START_LINE
  ADD", but 0x40 encodes its start line in the command byte itself and takes
  no argument, so that 0x2E lands as a command in its own right.

The 30+ min soak and `py-spy` frame-path confirmation both passed — see
`PROGRESS.md` session 5. `hw_scroll_spike.py` above is the one remaining
open item, driver or otherwise.
