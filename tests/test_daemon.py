import fcntl

import pytest

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
