"""Horizontal slide transition: pure numpy over packed framebuffers."""

import numpy as np
import pytest

from oled_hud.hud import PAGES, WIDTH
from oled_hud.hud.compositor import plan_run
from oled_hud.hud.transition import Slide, slide


def numbered(fill: int) -> np.ndarray:
    """A (PAGES, WIDTH) array where every column holds a distinct value,
    so a slide's column boundary is checkable by exact value, not just
    "changed vs. unchanged"."""
    return np.tile(np.arange(WIDTH, dtype=np.uint8) + fill, (PAGES, 1))


OLD = numbered(0)     # columns are 0..127
NEW = numbered(200)   # columns are 200..327, wrapped into uint8


# -- slide() ----------------------------------------------------------------

def test_offset_zero_is_the_outgoing_frame():
    assert np.array_equal(slide(OLD, NEW, 0), OLD)


def test_a_full_offset_is_the_incoming_frame():
    assert np.array_equal(slide(OLD, NEW, WIDTH), NEW)


def test_a_mid_slide_frame_is_old_columns_then_new_columns():
    out = slide(OLD, NEW, 40)
    assert np.array_equal(out[:, :88], OLD[:, 40:])   # old's tail, shifted left
    assert np.array_equal(out[:, 88:], NEW[:, :40])   # new's head, entering right


def test_offset_is_clamped_to_the_panel_width():
    assert np.array_equal(slide(OLD, NEW, -10), OLD)
    assert np.array_equal(slide(OLD, NEW, WIDTH + 50), NEW)


def test_direction_reverses_which_side_the_new_view_enters_from():
    left_to_right = slide(OLD, NEW, 40, direction=1)
    assert np.array_equal(left_to_right[:, :40], NEW[:, WIDTH - 40 :])
    assert np.array_equal(left_to_right[:, 40:], OLD[:, : WIDTH - 40])


def test_slide_never_unpacks():
    out = slide(OLD, NEW, 64)
    assert out.shape == (PAGES, WIDTH)
    assert out.dtype == np.uint8


def test_slide_returns_a_new_array_not_a_view():
    out = slide(OLD, NEW, 64)
    out[:] = 0
    assert OLD.any() and NEW.any()  # inputs untouched


# -- Slide --------------------------------------------------------------

def test_the_slide_is_time_driven_not_frame_driven():
    # Two different tick counts over the same elapsed time land on the same
    # offset -- a transition takes 0.3s whatever the fps or dropped frames.
    s = Slide(OLD, NEW, duration=0.3)
    assert s.offset(0.15) == s.offset(0.15)
    a = Slide(OLD, NEW, duration=0.3)
    b = Slide(OLD, NEW, duration=0.3)
    for t in (0.05, 0.1, 0.15, 0.2, 0.25):
        assert a.offset(t) == b.offset(t)


def test_offset_is_monotonic_over_the_duration():
    s = Slide(OLD, NEW, duration=0.3)
    offsets = [s.offset(t) for t in np.linspace(0, 0.3, 30)]
    assert offsets == sorted(offsets)


def test_offset_starts_at_zero_and_ends_at_the_full_width():
    s = Slide(OLD, NEW, duration=0.3)
    assert s.offset(0.0) == 0
    assert s.offset(0.3) == WIDTH


def test_compose_reports_running_before_duration_and_done_after():
    s = Slide(OLD, NEW, duration=0.3)
    out = np.zeros((PAGES, WIDTH), dtype=np.uint8)
    assert s.compose(out, 0.0) is True
    assert s.compose(out, 0.15) is True
    assert s.compose(out, 0.3) is False


def test_the_final_frame_is_byte_identical_to_the_target():
    # The property that matters for push counting: a frame that differs from
    # `new` by even one byte forces a second, wasted full-width push right
    # after the slide already finished visually.
    s = Slide(OLD, NEW, duration=0.3)
    out = np.zeros((PAGES, WIDTH), dtype=np.uint8)
    s.compose(out, 0.3)
    assert np.array_equal(out, NEW)
    s.compose(out, 999.0)  # well past duration -- still exactly NEW
    assert np.array_equal(out, NEW)


def test_a_negative_duration_is_rejected():
    with pytest.raises(ValueError):
        Slide(OLD, NEW, duration=0.0)


def test_retarget_swaps_the_incoming_frame_without_restarting_progress():
    s = Slide(OLD, NEW, duration=0.3)
    out = np.zeros((PAGES, WIDTH), dtype=np.uint8)
    s.compose(out, 0.15)
    mid_offset_before = s.offset(0.15)
    replacement = numbered(50)
    s.retarget(replacement)
    assert s.offset(0.15) == mid_offset_before  # progress unaffected
    s.compose(out, 0.3)
    assert np.array_equal(out, replacement)  # but the target changed


def test_a_slide_frame_dirties_the_full_width():
    # Pins this design's cost claim to the actual compositor model: a
    # mid-slide frame's dirty columns span the whole panel, so plan_run
    # picks one wide push rather than several narrow ones.
    s = Slide(OLD, NEW, duration=0.3)
    out = OLD.copy()  # "pushed" state before the slide starts
    s.compose(out, 0.15)
    dirty = out != OLD
    assert dirty.any(axis=0).all()  # every column changed
    pushes = plan_run(dirty, 0, PAGES - 1)
    assert pushes == [(0, PAGES - 1, 0, WIDTH - 1)]
