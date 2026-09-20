import fcntl
import threading

import numpy as np
import pytest

from oled_hud.clock import FrameClock
from oled_hud.hud import daemon
from oled_hud.hud.compositor import PAGES, WIDTH, Compositor
from oled_hud.hud.daemon import acquire_singleton_lock, build_views, run_loop
from oled_hud.hud.font import load
from oled_hud.hud.scheduler import Scheduler
from oled_hud.hud.store import Store
from oled_hud.hud.views import ClockView, SysView, View


def test_second_instance_exits_immediately(tmp_path):
    path = str(tmp_path / "oled-hud.lock")
    first = acquire_singleton_lock(path)
    try:
        with pytest.raises(SystemExit, match="already running"):
            acquire_singleton_lock(path)
    finally:
        first.close()


def test_lock_is_released_on_close(tmp_path):
    path = str(tmp_path / "oled-hud.lock")
    first = acquire_singleton_lock(path)
    first.close()  # simulates clean shutdown, and what the kernel does on a crash

    second = acquire_singleton_lock(path)  # must not raise
    second.close()


def test_lock_file_is_actually_flocked(tmp_path):
    """acquire_singleton_lock isn't just opening the file -- flock must be held."""
    path = str(tmp_path / "oled-hud.lock")
    held = acquire_singleton_lock(path)
    try:
        probe = open(path, "w")
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        probe.close()
    finally:
        held.close()


class FakeDisplay:
    """Records the panel-clearing calls the finally block must make."""

    def __init__(self):
        self.calls = []

    def fill(self, value):
        self.calls.append(("fill", value))

    def show(self):
        self.calls.append(("show",))


def test_a_setup_failure_still_clears_the_panel(tmp_path, monkeypatch):
    """The panel is live from reset_display() onward, so anything that fails
    after it -- compositor, font, producer thread -- must still reach the
    finally. A lit panel showing a half-drawn frame is the failure mode this
    module's singleton-and-reset design exists to prevent.
    """
    display = FakeDisplay()
    monkeypatch.setattr(daemon, "reset_display", lambda: display)

    def explode(_driver):
        raise RuntimeError("compositor failed to build")

    monkeypatch.setattr(daemon, "Compositor", explode)

    with pytest.raises(RuntimeError, match="compositor failed"):
        daemon.main(["--lock-path", str(tmp_path / "oled-hud.lock")])

    assert ("fill", 0) in display.calls
    assert ("show",) in display.calls


def test_a_setup_failure_releases_the_lock(tmp_path, monkeypatch):
    """...and the next launch must be able to start, not find a stale lock."""
    path = str(tmp_path / "oled-hud.lock")
    monkeypatch.setattr(daemon, "reset_display", lambda: FakeDisplay())
    monkeypatch.setattr(daemon, "Compositor", lambda _d: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        daemon.main(["--lock-path", path])

    second = acquire_singleton_lock(path)  # must not raise
    second.close()


# -- run_loop() ---------------------------------------------------------

class FakeDriver:
    """Records blit() calls instead of touching I2C -- same shape as
    test_compositor.py's, so run_loop() is drivable with no hardware."""

    def __init__(self):
        self.calls = []

    def blit(self, data, *, col, ncols, page0, page1):
        self.calls.append((data, col, ncols, page0, page1))


class ConstView(View):
    """Fills the framebuffer with a fixed byte, ignoring the snapshot --
    used where a test only needs *a* render to happen, not real content."""

    name = "const"

    def __init__(self, value: int, refresh: float = 0.0):
        self.value = value
        self.refresh = refresh

    def render_into(self, fb: np.ndarray, snap, now: float) -> None:
        fb[...] = self.value


def make_loop_fixture(*, views=None, seconds=0.05):
    comp = Compositor(FakeDriver())
    store = Store()
    sched = Scheduler(views or [ConstView(0x11)], dwell=1000.0, transition=0)
    clock = FrameClock(1000.0)  # fast, so a short `seconds` still ticks several times
    stopping = threading.Event()
    return comp, store, sched, clock, stopping, seconds


def test_the_loop_pushes_nothing_while_nothing_changes():
    comp, store, sched, clock, stopping, seconds = make_loop_fixture()
    renders, pushes, frames_pushing = run_loop(comp, store, sched, clock, stopping, seconds=seconds)
    # One real render (the initial view appearing) and one real push for it;
    # every frame after that must diff to nothing, since nothing changed.
    assert renders == 1
    assert frames_pushing == 1


def test_the_loop_counts_renders_and_pushes_separately():
    # A view with its own refresh cadence renders repeatedly without
    # necessarily pushing every time -- renders and frames_pushing must be
    # tracked independently, not conflated into one counter.
    comp, store, sched, clock, stopping, seconds = make_loop_fixture(
        views=[ConstView(0x11, refresh=0.001)], seconds=0.05,
    )
    renders, pushes, frames_pushing = run_loop(comp, store, sched, clock, stopping, seconds=seconds)
    assert renders > frames_pushing >= 1  # later same-value renders push nothing


def test_a_stop_event_exits_the_loop_promptly():
    comp, store, sched, clock, stopping, _ = make_loop_fixture(seconds=0.0)
    stopping.set()  # already stopped before the loop starts
    renders, pushes, frames_pushing = run_loop(comp, store, sched, clock, stopping)
    assert renders == 0
    assert pushes == 0


def test_the_view_list_argument_selects_and_orders_the_rotation():
    font = load("spleen")
    views = build_views(["clock", "sys"], font)
    assert [type(v) for v in views] == [ClockView, SysView]
    with pytest.raises(ValueError):
        build_views(["not-a-real-view"], font)
