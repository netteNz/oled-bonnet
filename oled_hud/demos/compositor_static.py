"""Phase H0 hardware demo -- a static view through the Compositor.

Renders one line of text once (PIL, off the frame path), packs it into
`Compositor.fb`, then runs a plain `FrameClock` loop that only ever calls
`flush()`. Nothing in the loop changes `fb` after the first frame, so this
is the diff engine's easiest case: frame 1 should push the dirty region
once, and every frame after should push nothing at all. Pushes per frame
are printed so that's visible on the panel run rather than assumed.

Run with:
    .env/bin/python3 -m oled_hud.demos.compositor_static
    .env/bin/python3 -m oled_hud.demos.compositor_static --seconds 10
"""

import argparse

import board
import busio
import digitalio
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from oled_hud.clock import FrameClock
from oled_hud.driver import PartialSSD1305
from oled_hud.hud.compositor import Compositor
from oled_hud.pack import pack_bits

WIDTH = 128
HEIGHT = 32
TEXT = "Phase H0 -- static view"


def render_into(comp: Compositor, text: str) -> None:
    """All PIL/font work happens here, once, before the frame loop starts."""
    font = ImageFont.load_default()
    img = Image.new("1", (WIDTH, HEIGHT))
    ImageDraw.Draw(img).text((2, 8), text, fill=255, font=font)
    bits = pack_bits(np.asarray(img, dtype=np.uint8) > 0)
    comp.fb[...] = bits


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--text", default=TEXT)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    display = PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)
    display.fill(0)
    display.show()  # panel and comp.pushed both start at all-zero

    comp = Compositor(display)
    render_into(comp, args.text)

    clock = FrameClock(args.fps)
    frame = 0
    try:
        while clock.elapsed < args.seconds:
            pushes = comp.flush()
            print(f"frame {frame}: {len(pushes)} push(es)")
            frame += 1
            clock.tick()
    except KeyboardInterrupt:
        pass
    finally:
        display.fill(0)
        display.show()
        print(clock.summary())


if __name__ == "__main__":
    main()
