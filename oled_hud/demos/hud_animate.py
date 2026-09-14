"""HUD playground -- animate real content straight through the Compositor
(H0), no Store/producers/scheduler scaffolding. Two widgets, both numpy
straight into `Compositor.fb`, no PIL in the loop:

  - a bouncing square on pages 0-1 (rows 0-15)
  - a scrolling random-walk sparkline on pages 2-3 (rows 16-31)

Both change every frame, so this exercises `plan_run()` on genuinely
moving content rather than the settle-to-zero static case
`compositor_static.py` covers -- a good sanity check that the diff engine
stays cheap once things are actually animating.

Run with:
    .env/bin/python3 -m oled_hud.demos.hud_animate
    .env/bin/python3 -m oled_hud.demos.hud_animate --seconds 20 --fps 60
"""

import argparse

import board
import busio
import digitalio
import numpy as np

from oled_hud.clock import FrameClock
from oled_hud.driver import PartialSSD1305
from oled_hud.hud.compositor import Compositor
from oled_hud.pack import pack_bits

WIDTH = 128
HEIGHT = 32
BALL = 6  # px, square sprite


def draw_ball(fb: np.ndarray, x: int) -> None:
    bits = np.zeros((16, WIDTH), dtype=bool)
    bits[4 : 4 + BALL, x : x + BALL] = True
    fb[0:2, :] = pack_bits(bits)


def step_sparkline(state: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Drop the oldest sample, append one new random-walk step off the
    newest -- a plain np.roll would wrap the dropped sample back in as the
    new one instead of continuing the walk.
    """
    new_last = int(np.clip(state[-1] + rng.integers(-2, 3), 0, 15))
    state[:-1] = state[1:]
    state[-1] = new_last
    return state


def draw_sparkline(fb: np.ndarray, heights: np.ndarray) -> None:
    rows = np.arange(16)[:, None]
    bits = rows >= (16 - heights)[None, :]
    fb[2:4, :] = pack_bits(bits)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fps", type=float, default=60.0)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--step", type=int, default=2, help="ball px/frame")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    display = PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)
    display.fill(0)
    display.show()

    comp = Compositor(display)
    rng = np.random.default_rng()
    heights = np.full(WIDTH, 8, dtype=np.int64)

    x, dx = 0, args.step
    clock = FrameClock(args.fps)
    pushes_total = 0
    bytes_total = 0
    try:
        while clock.elapsed < args.seconds:
            x += dx
            if x <= 0 or x >= WIDTH - BALL:
                dx = -dx
                x = max(0, min(x, WIDTH - BALL))
            draw_ball(comp.fb, x)

            heights = step_sparkline(heights, rng)
            draw_sparkline(comp.fb, heights)

            pushes = comp.flush()
            pushes_total += len(pushes)
            bytes_total += sum((p1 - p0 + 1) * (c1 - c0 + 1) for p0, p1, c0, c1 in pushes)
            clock.tick()
    except KeyboardInterrupt:
        pass
    finally:
        display.fill(0)
        display.show()
        print(clock.summary())
        print(f"pushes: {pushes_total} total ({pushes_total / max(clock.frames, 1):.2f}/frame), "
              f"{bytes_total} bytes ({bytes_total / max(clock.frames, 1):.1f}/frame)")


if __name__ == "__main__":
    main()
