"""HUD daemon entrypoint (Phase H3: lifecycle).

Owns process lifecycle only -- singleton lock, hardware reset before
touching GDDRAM, `force_full()` so the first flush establishes known
state, and a clean SIGTERM/SIGINT shutdown -- so it's the same entrypoint
H1's producers and H2's scheduler plug into later without this module
changing shape.

This phase exists because of the orphaned-`ticker.py` incident in
PROGRESS.md session 5: two processes on the I2C bus corrupted the panel
silently, and only a hardware reset (not a plain `fill(0)`) cleared it.
Singleton-by-flock and reset-on-startup are the two things that make that
unrepeatable.

Until H1 (Store/producers) and H2 (views/scheduler) land there's nothing
real to schedule, so the render loop draws one placeholder view (name +
uptime) through the real `Compositor` -- enough to make
`systemctl --user restart` / `kill -9` recovery visually verifiable on the
panel today, not just testable in the abstract. `render_placeholder()` is
expected to be replaced wholesale once `views.py`/`scheduler.py` exist.
"""

import argparse
import fcntl
import os
import signal
import sys
import time

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
FPS = 60.0
DEFAULT_LOCK_PATH = os.path.expanduser("~/.oled-hud.lock")


def acquire_singleton_lock(path: str = DEFAULT_LOCK_PATH):
    """Exclusive, non-blocking flock on `path`. Exits the process if another
    instance already holds it. The returned file object must be kept open
    for the life of the process -- the kernel releases an flock on close,
    including on a crash, which a pidfile does not give you for free.
    """
    lock = open(path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        sys.exit("oled-hud already running")
    return lock


def reset_display() -> PartialSSD1305:
    """Toggle the reset pin and replay init_display() by constructing fresh
    -- `_SSD1305.poweron()`/`init_display()` do both as part of __init__.
    Never assume the panel is in a known state: a previous instance may
    have died mid-blit with the column window half-set (session 5).
    """
    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    return PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)


def render_placeholder(comp: Compositor, font, start: float) -> None:
    img = Image.new("1", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    draw.text((2, 2), "oled-hud", fill=255, font=font)
    draw.text((2, 16), f"up {int(time.monotonic() - start)}s", fill=255, font=font)
    comp.fb[...] = pack_bits(np.asarray(img, dtype=np.uint8) > 0)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fps", type=float, default=FPS)
    parser.add_argument("--lock-path", default=DEFAULT_LOCK_PATH)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    lock = acquire_singleton_lock(args.lock_path)  # before any hardware I/O
    display = reset_display()
    comp = Compositor(display)
    comp.force_full()

    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))

    font = ImageFont.load_default()
    clock = FrameClock(args.fps)
    start = time.monotonic()
    last_second = -1
    try:
        while not stopping:
            now = int(clock.elapsed)
            if now != last_second:
                render_placeholder(comp, font, start)
                last_second = now
            comp.flush()
            clock.tick()
    except KeyboardInterrupt:
        pass
    finally:
        display.fill(0)
        display.show()
        lock.close()  # releases the flock
        print(clock.summary())


if __name__ == "__main__":
    main()
