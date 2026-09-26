"""HUD daemon entrypoint (Phase H3 lifecycle + Phase H1 content).

Owns process lifecycle -- singleton lock, hardware reset before touching
GDDRAM, `force_full()` so the first flush establishes known state, and a
clean shutdown on either stop signal -- SIGTERM through a handler, SIGINT as
the KeyboardInterrupt it already arrives as -- and drives a Store, a producer
thread and (Phase H2) a `Scheduler` rotating over several views on top of it.

The lifecycle half exists because of the orphaned-`ticker.py` incident in
PROGRESS.md session 5: two processes on the I2C bus corrupted the panel
silently, and only a hardware reset (not a plain `fill(0)`) cleared it.
Singleton-by-flock and reset-on-startup are the two things that make that
unrepeatable.

The frame loop re-renders when `Scheduler.frame()` says to -- a data change
(the H1 `store.version` check, still the common case), a view's own refresh
cadence (a clock), a dwell timer expiring (rotation) or an alert appearing or
clearing (preemption). Producers run on multi-second cadences while the
panel runs at 60fps, so re-rendering every frame would rebuild an identical
framebuffer hundreds of times per update; gating on the scheduler's decision
keeps most frames in the Compositor's push-nothing path, though H2's
rotation and slide transitions spend some of that -- see `scheduler.py` and
`transition.py` for the accounting. Nothing in the loop touches PIL -- text
comes from the vendored bitmap fonts in `oled_hud.hud.font`, which is what
keeps the handoff's "zero Pillow/font calls in the frame path" criterion true
here and not just in the ticker.
"""

import argparse
import fcntl
import os
import signal
import sys
import threading
from collections.abc import Callable

import board
import busio
import digitalio

from oled_hud.clock import FrameClock
from oled_hud.driver import PartialSSD1305
from oled_hud.hud import HEIGHT, WIDTH
from oled_hud.hud.alerts import Alerts
from oled_hud.hud.compositor import Compositor
from oled_hud.hud.font import Font, load as load_font, names as font_names
from oled_hud.hud.portfolio import CoinbasePortfolio
from oled_hud.hud.producers import Producer, ProducerThread, default_producers
from oled_hud.hud.scheduler import Scheduler
from oled_hud.hud.store import Store
from oled_hud.hud.views import AlertView, ClockView, PortfolioView, SysView, View

FPS = 60.0
DEFAULT_LOCK_PATH = os.path.expanduser("~/.oled-hud.lock")

#: Rotation views selectable by name on `--views`. A plain dict rather than
#: letting views.py register themselves -- there's no runtime reason a view
#: needs to know its own CLI name, and keeping the mapping here means adding
#: a view never means touching views.py just to make it reachable.
VIEW_FACTORIES: dict[str, Callable[[Font], View]] = {
    "sys": SysView,
    "clock": ClockView,
    "portfolio": PortfolioView,
}

#: Producers a view needs beyond the always-on local-system set, started only
#: when that view is in the rotation -- so a daemon without `portfolio` never
#: imports the Coinbase SDK, reads credentials or touches the network.
VIEW_PRODUCERS: dict[str, Callable[[], Producer]] = {
    "portfolio": CoinbasePortfolio,
}


def build_producers(names: list[str]) -> list[Producer]:
    return default_producers() + [VIEW_PRODUCERS[n]() for n in names if n in VIEW_PRODUCERS]


def build_views(names: list[str], font: Font) -> list[View]:
    views = []
    for name in names:
        if name not in VIEW_FACTORIES:
            raise ValueError(f"unknown view {name!r}, have {sorted(VIEW_FACTORIES)}")
        views.append(VIEW_FACTORIES[name](font))
    return views


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
    parser.add_argument("--fps", type=float, default=FPS,
                        help="frame loop rate")
    parser.add_argument("--font", choices=font_names(), default="spleen",
                        help="bitmap font; sets the line/column budget")
    parser.add_argument("--lock-path", default=DEFAULT_LOCK_PATH,
                        help="singleton flock path")
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="run time; 0 runs until signalled")
    parser.add_argument("--stats", action="store_true",
                        help="print push/render counts on exit")
    parser.add_argument("--views", default="sys,clock",
                        help=f"comma-separated rotation order, from {sorted(VIEW_FACTORIES)}")
    parser.add_argument("--dwell", type=float, default=8.0,
                        help="seconds each view holds before rotating")
    parser.add_argument("--transition", choices=["slide", "none"], default="slide",
                        help="how the panel switches between views")
    parser.add_argument("--transition-s", type=float, default=0.3,
                        help="slide duration in seconds")
    parser.add_argument("--no-alerts", action="store_true",
                        help="disable threshold-alert preemption of the rotation")
    return parser.parse_args(argv)


def run_loop(comp: Compositor, store: Store, sched: Scheduler, clock: FrameClock,
             stopping: threading.Event, *, seconds: float = 0.0) -> tuple[int, int, int]:
    """The frame loop itself, pulled out of `main()` so it's drivable against
    a fake driver and a fake clock with no hardware behind either -- the
    render-gate accounting (renders vs. pushes vs. frames actually pushing)
    is exactly the thing H2 changes and exactly the thing worth pinning with
    a regression test.

    `now` passed to `sched.frame()` is `store.now()`, not `clock.elapsed`:
    `Reading.fresh()` compares against the same clock a `Reading`'s
    timestamp was stamped with (`Store`'s, `time.monotonic` by default), and
    `clock.elapsed` is a *different* clock zeroed at `FrameClock` construction
    -- feeding that into freshness or dwell/slide timing would compare two
    unrelated origins and make every reading look either permanently fresh
    or permanently stale depending on which clock happened to be larger.
    """
    renders = pushes = frames_pushing = 0
    while not stopping.is_set() and (not seconds or clock.elapsed < seconds):
        if sched.frame(comp.fb, store, store.now()):
            renders += 1
        made = comp.flush()
        if made:
            pushes += len(made)
            frames_pushing += 1
        clock.tick()
    return renders, pushes, frames_pushing


def main(argv=None):
    args = parse_args(argv)

    lock = acquire_singleton_lock(args.lock_path)  # before any hardware I/O
    display = reset_display()

    # Everything from here on is inside the try: force_full() has already
    # been promised to the panel, so a failure while building the compositor,
    # the font, the producer thread or the scheduler must still reach the
    # finally that clears it. Setting up outside the try was how the panel
    # could be left lit with a half-drawn frame -- the exact state this
    # module exists to make impossible.
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())

    producers = None
    sched = None
    clock = FrameClock(args.fps)
    renders = pushes = frames_pushing = 0
    try:
        comp = Compositor(display)
        comp.force_full()
        store = Store()
        view_names = [n for n in args.views.split(",") if n]
        font = load_font(args.font)
        views = build_views(view_names, font)
        producer_list = build_producers(view_names)
        for producer in producer_list:
            try:
                producer.warm()
            except Exception as exc:  # e.g. network down; poll() retries it
                print(f"warm-up failed for {producer.name}: {exc}")
        producers = ProducerThread(store, producer_list).start()
        transition_s = 0.0 if args.transition == "none" else args.transition_s
        alerts = None if args.no_alerts else Alerts()
        alert_view = None if args.no_alerts else AlertView(font)
        sched = Scheduler(views, dwell=args.dwell, alerts=alerts,
                          alert_view=alert_view, transition=transition_s)

        # Setup -- producer warm-up especially, ~1s for the Coinbase SDK --
        # happened on this clock's time. Without a reset, frame 1 is due
        # before the loop even starts and gets booked as ~60 dropped frames.
        clock.reset()
        renders, pushes, frames_pushing = run_loop(
            comp, store, sched, clock, stopping, seconds=args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        if producers is not None:
            producers.stop()
        display.fill(0)
        display.show()
        lock.close()  # releases the flock
        print(clock.summary())
        if producers is not None:
            print(producers.summary())
        if sched is not None:
            print(sched.summary())
        if args.stats:
            print(f"renders: {renders}, pushes: {pushes} over {frames_pushing}/{clock.frames} frames")


if __name__ == "__main__":
    main()
