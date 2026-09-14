"""Phase 4 demo — the ticker, paced by FrameClock and dressed with effects.

Combines the Phase 2 packed tape with Phase 4's pacing: the tape is
rasterized once up front, `FrameClock` holds a fixed fps without
cumulative drift, and the slack it measured is printed on exit — so the
frame budget is a number from the panel rather than an assumption.

The frame loop below touches no PIL, font or packing code: it slices
pre-packed bytes out of the `Tape`, blits them, and lets the effect queue
push at most a couple of command bytes. Ctrl+C to stop; the screen is
cleared and the stats printed on exit.

Run with:
    .env/bin/python3 -m oled_hud.demos.ticker
    .env/bin/python3 -m oled_hud.demos.ticker --fps 40 --seconds 60
    .env/bin/python3 -m oled_hud.demos.ticker --seconds 1800   # 30-min soak
"""

import argparse

import board
import busio
import digitalio
from PIL import Image, ImageDraw, ImageFont

from oled_hud.clock import FrameClock
from oled_hud.driver import PartialSSD1305
from oled_hud.effects import EffectQueue, Fade, Flash
from oled_hud.tape import Tape

WIDTH = 128
HEIGHT = 32
PAGE0, PAGE1 = 2, 3  # bottom two pages (16px tall) host the ticker

TEXT = "The quick brown fox jumps over the lazy dog -- Phase 4 ticker on FrameClock -- "
CONTRAST = 0xCF


def render_tape(text: str) -> Tape:
    """All PIL/font work happens here, once, before the frame loop starts."""
    font = ImageFont.load_default()
    bbox = font.getbbox(text)
    img = Image.new("1", (bbox[2] - bbox[0] + 4, 16))
    ImageDraw.Draw(img).text((0, 0), text, fill=255, font=font)
    return Tape(img)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fps", type=float, default=30.0, help="target frame rate")
    parser.add_argument(
        "--seconds", type=float, default=0.0, help="run time; 0 runs until Ctrl+C"
    )
    parser.add_argument("--step", type=int, default=2, help="scroll speed in px/frame")
    parser.add_argument("--text", default=TEXT, help="string to scroll")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    tape = render_tape(args.text)

    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    display = PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)
    display.fill(0)
    display.show()

    effects = EffectQueue()
    effects.play(Flash(0.06))
    effects.play(Fade(0, CONTRAST, 0.8))

    clock = FrameClock(args.fps)
    x = 0
    try:
        while not args.seconds or clock.elapsed < args.seconds:
            display.blit(tape.frame(x), col=0, ncols=WIDTH, page0=PAGE0, page1=PAGE1)
            effects.update(display, clock.elapsed)
            x += args.step
            clock.tick()
    except KeyboardInterrupt:
        pass
    finally:
        display.fill(0)
        display.show()
        print(clock.summary())


if __name__ == "__main__":
    main()
