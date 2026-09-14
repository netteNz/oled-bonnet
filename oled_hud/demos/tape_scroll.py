"""Phase 2 demo — scrolls a text ticker on pages 2-3 via Tape.frame() -> blit().

Rasterizes the string once up front into a Tape, then the frame loop below
does no PIL/font work at all: each tick only slices pre-packed bytes out of
the Tape and blits them. Ctrl+C to stop; the screen is cleared on exit.

Run with: .env/bin/python3 -m oled_hud.demos.tape_scroll
"""

import time

import board
import busio
import digitalio
from PIL import Image, ImageDraw, ImageFont

from oled_hud.driver import PartialSSD1305
from oled_hud.tape import Tape

WIDTH = 128
HEIGHT = 32
PAGE0, PAGE1 = 2, 3  # bottom two pages (16px tall) host the ticker

TEXT = "The quick brown fox jumps over the lazy dog -- Phase 2 packed-tape ticker -- "
SCROLL_STEP = 2  # px per frame
FRAME_DELAY = 0.03  # s per frame (~33 fps)


def _render_tape() -> Tape:
    font = ImageFont.load_default()
    bbox = font.getbbox(TEXT)
    text_w = bbox[2] - bbox[0]
    img = Image.new("1", (text_w + 4, 16))
    draw = ImageDraw.Draw(img)
    draw.text((0, 0), TEXT, fill=255, font=font)
    return Tape(img)


def main():
    tape = _render_tape()  # all PIL/font work happens here, once

    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    display = PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)
    display.fill(0)
    display.show()

    x = 0
    try:
        while True:
            display.blit(tape.frame(x), col=0, ncols=WIDTH, page0=PAGE0, page1=PAGE1)
            x += SCROLL_STEP
            time.sleep(FRAME_DELAY)
    except KeyboardInterrupt:
        pass
    finally:
        display.fill(0)
        display.show()


if __name__ == "__main__":
    main()
