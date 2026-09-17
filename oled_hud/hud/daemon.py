"""HUD daemon entrypoint (Phase H3 lifecycle + Phase H1 content).

Owns process lifecycle -- singleton lock, hardware reset before touching
GDDRAM, `force_full()` so the first flush establishes known state, and a
clean SIGTERM/SIGINT shutdown -- and drives a Store, a producer thread and a
view on top of it.

The lifecycle half exists because of the orphaned-`ticker.py` incident in
PROGRESS.md session 5: two processes on the I2C bus corrupted the panel
silently, and only a hardware reset (not a plain `fill(0)`) cleared it.
Singleton-by-flock and reset-on-startup are the two things that make that
unrepeatable.

The frame loop re-renders only when `store.version` changes. Producers run
on multi-second cadences while the panel runs at 60fps, so re-rendering
every frame would rebuild an identical framebuffer hundreds of times per
update; gating on the version keeps almost every frame in the Compositor's
push-nothing path. Nothing in the loop touches PIL -- text comes from the
vendored bitmap fonts in `oled_hud.hud.font`, which is what keeps the
handoff's "zero Pillow/font calls in the frame path" criterion true here and
not just in the ticker.

H2 replaces the single fixed view below with a scheduler over several.
"""

import argparse
import fcntl
import os
import signal
import sys

import board
import busio
import digitalio

from oled_hud.clock import FrameClock
from oled_hud.driver import PartialSSD1305
from oled_hud.hud.compositor import Compositor
from oled_hud.hud.font import load as load_font, names as font_names
from oled_hud.hud.producers import ProducerThread
from oled_hud.hud.store import Store
from oled_hud.hud.views import SysView

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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fps", type=float, default=FPS)
    parser.add_argument("--font", choices=font_names(), default="spleen")
    parser.add_argument("--lock-path", default=DEFAULT_LOCK_PATH)
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="run time; 0 runs until signalled")
    parser.add_argument("--stats", action="store_true",
                        help="print push/render counts on exit")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    lock = acquire_singleton_lock(args.lock_path)  # before any hardware I/O
    display = reset_display()
    comp = Compositor(display)
    comp.force_full()

    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))

    store = Store()
    producers = ProducerThread(store).start()
    view = SysView(load_font(args.font))

    clock = FrameClock(args.fps)
    seen = -1
    renders, pushes, frames_pushing = 0, 0, 0
    try:
        while not stopping and not (args.seconds and clock.elapsed >= args.seconds):
            version = store.version
            if version != seen:
                view.render_into(comp.fb, store.snapshot(), store.now())
                seen = version
                renders += 1
            made = comp.flush()
            if made:
                pushes += len(made)
                frames_pushing += 1
            clock.tick()
    except KeyboardInterrupt:
        pass
    finally:
        producers.stop()
        display.fill(0)
        display.show()
        lock.close()  # releases the flock
        print(clock.summary())
        print(producers.summary())
        if args.stats:
            print(f"renders: {renders}, pushes: {pushes} over {frames_pushing}/{clock.frames} frames")


if __name__ == "__main__":
    main()
