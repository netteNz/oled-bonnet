import fcntl

import pytest

from oled_hud.hud import daemon
from oled_hud.hud.daemon import acquire_singleton_lock


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
