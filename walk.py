"""Stick figure walk demo — bounces a small sprite back and forth across the
display using oled_hud.driver.PartialSSD1305.blit(), pushing only the
column range that changed each frame (sprite width + step) instead of a
full 512-byte frame. Ctrl+C to stop; the screen is cleared on exit.
"""

import math
import time

import board
import busio
import digitalio
from PIL import Image, ImageDraw

from oled_hud.driver import PartialSSD1305

WIDTH = 128
HEIGHT = 32
NPAGES = HEIGHT // 8

SPRITE_W = 14
N_POSES = 8
SWING = 4

MOVE_STEP = 2  # px per frame
FRAME_DELAY = 0.04  # s per frame (~25 fps)


def stick_figure(phase: int) -> Image.Image:
    img = Image.new("1", (SPRITE_W, HEIGHT))
    draw = ImageDraw.Draw(img)
    swing = round(SWING * math.sin(2 * math.pi * phase / N_POSES))

    cx = SPRITE_W // 2
    draw.ellipse((cx - 3, 2, cx + 3, 8), outline=255, fill=0)
    draw.line((cx, 8, cx, 20), fill=255)
    draw.line((cx, 11, cx + swing, 17), fill=255)
    draw.line((cx, 11, cx - swing, 17), fill=255)
    draw.line((cx, 20, cx + swing, 30), fill=255)
    draw.line((cx, 20, cx - swing, 30), fill=255)
    return img


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


def _blit_region(display, canvas, col0, col1):
    if col1 <= col0:
        return
    region = canvas.crop((col0, 0, col1, HEIGHT))
    display.blit(image_to_pages(region), col=col0, ncols=col1 - col0, page0=0, page1=NPAGES - 1)


def _walk_pass(display, canvas, draw, phase, x, step, x_end):
    """Walk from `x` toward `x_end` (exclusive) in `step`-px increments,
    returning the phase/x/last-drawn-box to resume from for the next pass."""
    prev_box = None
    while (step > 0 and x < x_end) or (step < 0 and x > x_end):
        sprite = stick_figure(phase)
        clip = (max(x, 0), 0, min(x + SPRITE_W, WIDTH), HEIGHT)

        dirty0 = max(min(prev_box[0], clip[0]) if prev_box else clip[0], 0)
        dirty1 = min(max(prev_box[2], clip[2]) if prev_box else clip[2], WIDTH)

        if prev_box:
            draw.rectangle(prev_box, fill=0)
        canvas.paste(sprite, (x, 0))

        _blit_region(display, canvas, dirty0, dirty1)

        prev_box = clip
        x += step
        phase = (phase + 1) % N_POSES
        time.sleep(FRAME_DELAY)

    return phase, x


def main():
    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    display = PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)
    display.fill(0)
    display.show()

    canvas = Image.new("1", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(canvas)

    phase = 0
    x = -SPRITE_W
    step = MOVE_STEP

    try:
        while True:
            x_end = WIDTH if step > 0 else -SPRITE_W
            phase, x = _walk_pass(display, canvas, draw, phase, x, step, x_end)
            step = -step
    except KeyboardInterrupt:
        pass
    finally:
        display.fill(0)
        display.show()


if __name__ == "__main__":
    main()
