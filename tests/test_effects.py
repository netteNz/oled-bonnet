import pytest

from oled_hud.effects import (
    CONTRAST_MAX,
    Blink,
    EffectQueue,
    Fade,
    Flash,
    all_on,
    contrast,
    invert,
)

ENTIRE_ON_RAM = 0xA4
ENTIRE_ON_ALL = 0xA5


class FakeDisplay:
    """Records what an effect would have pushed to the panel."""

    def __init__(self):
        self.contrasts = []
        self.inverts = []
        self.cmds = []

    def contrast(self, value):
        self.contrasts.append(value)

    def invert(self, value):
        self.inverts.append(value)

    def write_cmd(self, cmd):
        self.cmds.append(cmd)


@pytest.mark.parametrize(
    "given,expected",
    [(0, 0), (128, 128), (255, 255), (-10, 0), (999, CONTRAST_MAX), (12.7, 12)],
)
def test_contrast_clamps_to_the_register_range(given, expected):
    display = FakeDisplay()
    assert contrast(display, given) == expected
    assert display.contrasts == [expected]


def test_invert_passes_a_bool():
    display = FakeDisplay()
    invert(display, 1)
    invert(display, 0)
    assert display.inverts == [True, False]


def test_all_on_sends_the_documented_commands():
    display = FakeDisplay()
    all_on(display, True)
    all_on(display, False)
    assert display.cmds == [ENTIRE_ON_ALL, ENTIRE_ON_RAM]


def test_fade_ramps_monotonically_and_lands_on_the_end_value():
    display = FakeDisplay()
    fade = Fade(0, 255, 1.0)

    assert fade.update(display, 0.0) is True
    assert fade.update(display, 0.5) is True
    assert fade.update(display, 1.0) is False

    assert display.contrasts[0] == 0
    assert display.contrasts[-1] == 255
    assert display.contrasts == sorted(display.contrasts)


def test_fade_down_works_too():
    display = FakeDisplay()
    fade = Fade(200, 20, 0.5)
    fade.update(display, 0.0)
    fade.update(display, 0.5)
    assert display.contrasts[0] == 200
    assert display.contrasts[-1] == 20


def test_fade_skips_redundant_writes():
    display = FakeDisplay()
    fade = Fade(100, 101, 1.0)
    for i in range(11):
        fade.update(display, i / 10)
    # Only two distinct values exist in the ramp; don't resend either.
    assert display.contrasts == [100, 101]


def test_fade_clamps_time_outside_its_window():
    display = FakeDisplay()
    fade = Fade(0, 255, 1.0)
    assert fade.update(display, -5.0) is True
    assert display.contrasts == [0]
    assert fade.update(display, 99.0) is False
    assert display.contrasts[-1] == 255


@pytest.mark.parametrize("duration", [0, -1])
def test_fade_rejects_a_non_positive_duration(duration):
    with pytest.raises(ValueError):
        Fade(0, 255, duration)


def test_blink_toggles_and_clears_itself_when_done():
    display = FakeDisplay()
    blink = Blink(period=1.0, times=2)
    assert blink.duration == pytest.approx(2.0)

    assert blink.update(display, 0.0) is True  # inverted
    assert blink.update(display, 0.6) is True  # normal
    assert blink.update(display, 1.2) is True  # inverted
    assert blink.update(display, 1.7) is True  # normal
    assert blink.update(display, 2.0) is False

    assert display.inverts == [True, False, True, False]
    assert display.inverts[-1] is False  # display left un-inverted


def test_blink_left_inverted_is_restored_on_the_final_update():
    display = FakeDisplay()
    blink = Blink(period=1.0, times=1)
    blink.update(display, 0.0)  # inverted
    assert display.inverts == [True]
    assert blink.update(display, 1.0) is False
    assert display.inverts == [True, False]


def test_blink_rejects_a_non_positive_period():
    with pytest.raises(ValueError):
        Blink(period=0)


def test_flash_lights_the_panel_then_returns_to_gddram():
    display = FakeDisplay()
    flash = Flash(0.1)

    assert flash.update(display, 0.0) is True
    assert flash.update(display, 0.05) is True
    assert flash.update(display, 0.1) is False

    assert display.cmds == [ENTIRE_ON_ALL, ENTIRE_ON_RAM]


def test_effects_are_time_driven_not_frame_driven():
    """Sparse updates (a dropped-frame run) must finish at the same wall time
    as dense ones."""
    dense, sparse = FakeDisplay(), FakeDisplay()

    fade_dense = Fade(0, 255, 1.0)
    for i in range(101):
        running = fade_dense.update(dense, i / 100)
    assert running is False

    fade_sparse = Fade(0, 255, 1.0)
    for t in (0.0, 0.5, 1.0):
        running = fade_sparse.update(sparse, t)
    assert running is False

    assert dense.contrasts[-1] == sparse.contrasts[-1] == 255


def test_queue_runs_effects_in_order():
    display = FakeDisplay()
    queue = EffectQueue()
    queue.play(Flash(0.1))
    queue.play(Fade(0, 255, 1.0))

    assert queue.busy is True
    assert queue.update(display, 10.0) is True  # flash starts at now=10
    assert display.cmds == [ENTIRE_ON_ALL]

    assert queue.update(display, 10.05) is True
    assert display.contrasts == []  # fade hasn't started yet

    # Flash ends and the fade starts in the same frame.
    assert queue.update(display, 10.15) is True
    assert display.cmds == [ENTIRE_ON_ALL, ENTIRE_ON_RAM]
    assert display.contrasts == [0]

    assert queue.update(display, 11.3) is False
    assert display.contrasts[-1] == 255
    assert queue.busy is False


def test_queue_start_time_is_relative_to_the_first_update():
    display = FakeDisplay()
    queue = EffectQueue()
    queue.play(Fade(0, 255, 1.0))

    queue.update(display, 500.0)
    assert display.contrasts == [0]
    assert queue.update(display, 500.5) is True
    assert display.contrasts[-1] == pytest.approx(128, abs=2)


def test_empty_queue_is_idle():
    display = FakeDisplay()
    queue = EffectQueue()
    assert queue.busy is False
    assert queue.update(display, 0.0) is False
    assert display.cmds == []
