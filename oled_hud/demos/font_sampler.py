"""Put a vendored bitmap font on the panel so it can be judged by eye.

The whole reason both fonts are vendored is that "25 chars of Spleen vs 32
chars of Tom Thumb" is not a decision that can be made from the numbers --
it depends on whether the smaller one is still readable on a 2.23" panel
across the room. This renders the printable ASCII range (or `--text`) so
that call can actually be made.

Static content, so it also doubles as a Compositor settle-to-zero check:
frame 0 pushes, every frame after it pushes nothing.

Run with:
    .env/bin/python3 -m oled_hud.demos.font_sampler --font spleen
    .env/bin/python3 -m oled_hud.demos.font_sampler --font tomthumb --seconds 30
    .env/bin/python3 -m oled_hud.demos.font_sampler --text "cpu 12%  49.9C"
"""

import argparse
import signal

import board
import busio
import digitalio
import numpy as np

from oled_hud.clock import FrameClock
from oled_hud.driver import PartialSSD1305
from oled_hud.hud.compositor import Compositor
from oled_hud.hud.font import load, names
from oled_hud.pack import pack_bits

WIDTH = 128
HEIGHT = 32


def sample_lines(font, text: str | None) -> list[str]:
    cols = WIDTH // font.advance
    if text is not None:
        return [text[i : i + cols] for i in range(0, len(text), cols)]
    printable = "".join(chr(c) for c in range(font.first, font.first + font.count))
    return [printable[i : i + cols] for i in range(0, len(printable), cols)]


def render(font, lines: list[str]) -> np.ndarray:
    canvas = np.zeros((HEIGHT, WIDTH), dtype=bool)
    for i, line in enumerate(lines[: HEIGHT // font.height]):
        font.draw(canvas, line, 0, i * font.height)
    return pack_bits(canvas)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--font", choices=names(), default="spleen")
    parser.add_argument("--text", default=None, help="show this instead of the ASCII range")
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--seconds", type=float, default=15.0, help="0 runs until Ctrl+C")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    font = load(args.font)
    lines = sample_lines(font, args.text)

    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    display = PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)
    display.fill(0)
    display.show()

    comp = Compositor(display)
    comp.fb[...] = render(font, lines)

    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))

    print(f"{args.font}: {font.width}x{font.height} cell, "
          f"{WIDTH // font.advance} cols x {HEIGHT // font.height} lines")
    for line in lines[: HEIGHT // font.height]:
        print(f"  |{line}|")

    clock = FrameClock(args.fps)
    pushing = 0
    try:
        while not stopping and not (args.seconds and clock.elapsed >= args.seconds):
            if comp.flush():
                pushing += 1
            clock.tick()
    except KeyboardInterrupt:
        pass
    finally:
        display.fill(0)
        display.show()
        print(clock.summary())
        print(f"frames that pushed anything: {pushing}/{clock.frames}")


if __name__ == "__main__":
    main()
