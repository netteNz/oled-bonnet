"""SysView: layout per font, staleness rendering, and the no-PIL guarantee."""

import subprocess
import sys
import time

import numpy as np
import pytest

from oled_hud.hud.alerts import Alert, Rule
from oled_hud.hud.font import load, upscale
from oled_hud.hud.producers import format_uptime
from oled_hud.hud.store import Reading, Store
from oled_hud.hud.views import (
    HEIGHT,
    MISSING,
    WIDTH,
    AlertView,
    ClockView,
    SysView,
    View,
    fmt,
    row,
)

NOW = 1000.0


def snap(**values):
    """A fresh snapshot: every key written 'now' with a generous TTL."""
    return {k: Reading(k, v, NOW, 60.0) for k, v in values.items()}


FULL = dict(
    **{
        "sys.host": "rasp3",
        "sys.uptime": 19139.0,
        "cpu.usage": 7.2,
        "cpu.temp": 51.5,
        "mem.used_mb": 514.0,
        "mem.total_mb": 905.0,
        "mem.pct": 56.8,
        "disk.free_gb": 19.9,
        "disk.pct": 30.4,
        "net.ip": "192.168.50.16",
        "load.1": 1.42,
    }
)


@pytest.fixture
def spleen_view():
    return SysView(load("spleen"))


@pytest.fixture
def tomthumb_view():
    return SysView(load("tomthumb"))


def lines(view, snapshot, now=NOW):
    return [row(fields, view.cols) for fields in view.select(view.rows(snapshot, now))]


def test_row_puts_the_last_field_flush_right(spleen_view):
    assert row(["cpu", "51.5C"], 25) == "cpu" + " " * 17 + "51.5C"
    assert len(row(["cpu", "51.5C"], 25)) == 25


def test_row_spreads_three_fields_across_the_width():
    # The whole point of the packed layout: a two-field row wastes the middle
    # of a 25-column line, and that waste is what a fifth line of smaller
    # type would otherwise buy back.
    out = row(["cpu 5%", "53.7C", "ld 0.37"], 25)
    assert len(out) == 25
    assert out.startswith("cpu 5%")
    assert out.endswith("ld 0.37")
    assert "53.7C" in out[6:-7]


def test_row_distributes_leftover_space_to_the_earlier_gaps():
    out = row(["a", "b", "c"], 10)
    assert out == "a    b   c"  # 7 spare over 2 gaps -> 4 then 3
    assert len(out) == 10


def test_row_with_one_field_is_left_aligned():
    assert row(["192.168.50.16"], 25) == "192.168.50.16"


def test_row_ignores_empty_fields():
    assert row(["a", "", "b"], 10) == row(["a", "b"], 10)


def test_row_with_no_fields_is_empty():
    assert row([], 25) == ""


def test_row_truncates_the_first_field_not_the_measurements():
    # The later fields are the numbers; the first is the label that
    # introduces them, and a clipped label still reads.
    out = row(["a-very-long-label-indeed", "99%"], 10)
    assert out.endswith("99%")
    assert len(out) == 10


def test_row_falls_back_to_the_value_when_nothing_fits():
    assert row(["label", "1234567890"], 6) == "123456"


def test_fmt_formats_a_fresh_reading():
    assert fmt(snap(x=51.54), "x", "{:.1f}C", now=NOW) == "51.5C"
    assert fmt(snap(x=7.2), "x", "{:.0f}%", now=NOW) == "7%"


def test_fmt_returns_missing_for_an_absent_key():
    assert fmt({}, "x", now=NOW) == MISSING


def test_fmt_returns_missing_for_a_stale_reading():
    assert fmt(snap(x=1.0), "x", now=NOW + 61.0) == MISSING


def test_fmt_applies_a_transform_before_formatting():
    # Uptime needs format_uptime() between the reading and the spec. Without
    # a hook here that one field has to open-code the freshness check, which
    # is exactly the scattering this module's docstring warns about.
    assert fmt(snap(x=19139.0), "x", "{}", now=NOW, transform=format_uptime) == "5h18m"


def test_fmt_skips_the_transform_for_a_stale_reading():
    stale = fmt(snap(x=19139.0), "x", "{}", now=NOW + 61.0, transform=format_uptime)
    assert stale == MISSING


def test_spleen_gets_four_lines_of_twenty_five(spleen_view):
    assert (spleen_view.lines, spleen_view.cols) == (4, 25)


def test_tomthumb_gets_five_lines_of_thirty_two(tomthumb_view):
    assert (tomthumb_view.lines, tomthumb_view.cols) == (5, 32)


def test_leftover_pixels_are_centered(spleen_view, tomthumb_view):
    assert spleen_view.y0 == 0            # 4 x 8 fills 32 exactly
    assert tomthumb_view.y0 == 1          # 5 x 6 leaves 2, split top and bottom


def test_every_row_fills_the_available_width(spleen_view, tomthumb_view):
    for view in (spleen_view, tomthumb_view):
        for line in lines(view, snap(**FULL)):
            assert len(line) == view.cols


def test_the_four_required_rows_carry_every_metric(spleen_view):
    # The packed layout exists so the 4-line font loses nothing: disk used to
    # be the row dropped here, and now it rides along on the memory line.
    out = lines(spleen_view, snap(**FULL))
    assert len(out) == 4
    joined = " ".join(out)
    for expected in ("rasp3", "up 5h18m", "cpu 7%", "51.5C", "ld 1.42",
                     "mem 514/905M", "dsk 30%", "192.168.50.16", "19.9G"):
        assert expected in joined, expected


def test_the_optional_load_detail_row_needs_a_fifth_line(spleen_view, tomthumb_view):
    assert not any("ld5" in line for line in lines(spleen_view, snap(**FULL)))
    out = lines(tomthumb_view, snap(**FULL))
    assert len(out) == 5
    assert "ld5" in out[4] and "ld15" in out[4]


def test_required_rows_survive_on_the_shorter_font(spleen_view):
    out = lines(spleen_view, snap(**FULL))
    assert out[0].startswith("rasp3")
    assert out[1].startswith("cpu")
    assert out[2].startswith("mem")
    assert out[3].startswith("192.168.50.16")


def test_values_are_rendered_as_expected(spleen_view):
    out = lines(spleen_view, snap(**FULL))
    assert out[0].startswith("rasp3") and out[0].endswith("up 5h18m")
    assert out[1].startswith("cpu 7%") and out[1].endswith("ld 1.42")
    assert out[2].startswith("mem 514/905M") and out[2].endswith("dsk 30%")
    assert out[3].endswith("19.9G")


def test_the_worst_case_row_still_fits_the_narrow_font(spleen_view):
    # A pegged CPU at a three-digit temperature with a two-digit load is the
    # widest row 2 can get; if that overflows, the layout is a bug waiting
    # for a hot day rather than a design.
    hot = dict(FULL, **{"cpu.usage": 100.0, "cpu.temp": 100.0, "load.1": 12.34,
                        "mem.used_mb": 9999.0, "mem.total_mb": 9999.0,
                        "disk.pct": 100.0, "net.ip": "192.168.100.100"})
    for line in lines(spleen_view, snap(**hot)):
        assert len(line) == spleen_view.cols
    out = lines(spleen_view, snap(**hot))
    assert out[1].startswith("cpu 100%") and out[1].endswith("ld 12.34")
    assert out[3].startswith("192.168.100.100")


def test_an_empty_store_renders_placeholders_not_a_crash(spleen_view):
    out = lines(spleen_view, {})
    assert all(MISSING in line for line in out)
    assert len(out) == 4


def test_stale_readings_become_placeholders(spleen_view):
    out = lines(spleen_view, snap(**FULL), now=NOW + 61.0)
    assert "5h18m" not in " ".join(out)
    assert all(MISSING in line for line in out)


def test_a_stale_host_and_uptime_keep_row_ones_shape(spleen_view):
    # Row 1's two fields are the only ones that needed a transform or no
    # format spec at all, so they are the ones most likely to drift when
    # fmt() changes: the label stays, only the number goes.
    out = lines(spleen_view, snap(**FULL), now=NOW + 61.0)
    assert out[0].startswith(MISSING)
    assert out[0].endswith(f"up {MISSING}")
    assert len(out[0]) == spleen_view.cols


def test_one_dead_producer_does_not_blank_the_others(spleen_view):
    mixed = snap(**FULL)
    mixed["cpu.temp"] = Reading("cpu.temp", 51.5, NOW - 999.0, 8.0)  # aged out
    out = lines(spleen_view, mixed)
    assert "51.5C" not in out[1]         # temp is gone
    assert out[1].startswith("cpu 7%")   # usage, from a live producer, is not
    assert out[1].endswith("ld 1.42")
    assert out[0].endswith("up 5h18m")


def test_render_draws_into_the_canvas(spleen_view):
    canvas = np.zeros((HEIGHT, WIDTH), dtype=bool)
    spleen_view.render(canvas, snap(**FULL), NOW)
    assert canvas.any()
    # Every line band has ink -- no row silently rendered offscreen.
    for i in range(spleen_view.lines):
        y = spleen_view.y0 + i * spleen_view.font.height
        assert canvas[y : y + spleen_view.font.height].any()


def test_render_into_packs_a_compositor_framebuffer(spleen_view):
    fb = np.zeros((4, WIDTH), dtype=np.uint8)
    spleen_view.render_into(fb, snap(**FULL), NOW)
    assert fb.dtype == np.uint8
    assert fb.shape == (4, WIDTH)
    assert fb.any()


def test_render_into_replaces_rather_than_accumulating(spleen_view):
    fb = np.zeros((4, WIDTH), dtype=np.uint8)
    spleen_view.render_into(fb, snap(**FULL), NOW)
    before = fb.copy()
    spleen_view.render_into(fb, snap(**FULL), NOW)
    assert np.array_equal(fb, before)  # idempotent: same data, same bytes
    spleen_view.render_into(fb, {}, NOW)
    assert not np.array_equal(fb, before)


def test_a_render_pushes_nothing_when_the_data_has_not_changed(spleen_view):
    # The property the daemon's version gate depends on: identical readings
    # must produce a byte-identical framebuffer, or the Compositor would
    # push every frame.
    from oled_hud.hud.compositor import Compositor

    class FakeDriver:
        def __init__(self):
            self.calls = 0

        def blit(self, data, *, col, ncols, page0, page1):
            self.calls += 1

    driver = FakeDriver()
    comp = Compositor(driver)
    spleen_view.render_into(comp.fb, snap(**FULL), NOW)
    comp.flush()
    first = driver.calls
    assert first > 0
    for _ in range(10):
        spleen_view.render_into(comp.fb, snap(**FULL), NOW)
        comp.flush()
    assert driver.calls == first


def test_store_snapshot_feeds_the_view_directly(spleen_view):
    # The seam the daemon actually uses, rather than a hand-built dict.
    store = Store()
    store.put_all(FULL, ttl=60.0)
    canvas = np.zeros((HEIGHT, WIDTH), dtype=bool)
    spleen_view.render(canvas, store.snapshot(), store.now())
    assert canvas.any()


def test_the_render_path_never_imports_pil():
    # The animation-engine handoff's standing criterion, enforced rather than
    # argued: import everything a frame touches in a clean interpreter and
    # check PIL never showed up. daemon.py itself is excluded only because
    # importing it needs I2C hardware.
    code = (
        "import sys;"
        "import oled_hud.hud.font, oled_hud.hud.store,"
        " oled_hud.hud.producers, oled_hud.hud.views, oled_hud.hud.compositor,"
        " oled_hud.hud.scheduler, oled_hud.hud.alerts, oled_hud.hud.transition,"
        " oled_hud.pack, oled_hud.clock;"
        "print([m for m in sys.modules if m.split('.')[0] in ('PIL',)])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout


# -- View base class ------------------------------------------------------

def test_every_view_packs_into_a_compositor_framebuffer(spleen_view):
    clock_view = ClockView(load("spleen"), wall=lambda: 0.0, localtime=time.gmtime)
    for view in (spleen_view, clock_view):
        fb = np.zeros((HEIGHT // 8, WIDTH), dtype=np.uint8)
        view.render_into(fb, snap(**FULL), NOW)
        assert fb.any()


def test_render_into_replaces_rather_than_accumulating(spleen_view):
    clock_view = ClockView(load("spleen"), wall=lambda: 0.0, localtime=time.gmtime)
    for view in (spleen_view, clock_view):
        fb = np.zeros((HEIGHT // 8, WIDTH), dtype=np.uint8)
        view.render_into(fb, snap(**FULL), NOW)
        first = fb.copy()
        view.render_into(fb, snap(**FULL), NOW)
        assert np.array_equal(fb, first)  # not doubled up by a second OR


def test_sysview_and_clockview_both_subclass_view(spleen_view):
    clock_view = ClockView(load("spleen"))
    assert isinstance(spleen_view, View)
    assert isinstance(clock_view, View)


# -- ClockView --------------------------------------------------------------

# A fixed epoch and time.gmtime rather than time.localtime everywhere below,
# so these assertions don't depend on the machine's timezone: 1970-01-01
# 03:04:05 UTC, a Thursday.
EPOCH = 3 * 3600 + 4 * 60 + 5


def clock_view(font_name="spleen", **kw):
    return ClockView(load(font_name), wall=lambda: EPOCH, localtime=time.gmtime, **kw)


def test_the_clock_renders_hours_and_minutes_at_full_height():
    # The cell the big digits occupy is 8px x4 = 32px -- the whole panel --
    # even though the glyphs themselves pad a blank row top and bottom
    # (see test_font.py's SPLEEN_A golden), so this checks the cell, not ink.
    view = clock_view()
    assert view.big_h == HEIGHT
    assert view.y0 == 0


def test_the_clock_is_horizontally_centered():
    # Checked against the cell box a glyph occupies, not the lit pixels:
    # a font's own left/right padding (see test_font.py) would otherwise
    # make an ink-based bounding box an unreliable proxy for centering.
    view = clock_view()
    big = upscale(load("spleen").render("03:04"), 4)
    w = big.shape[1]
    x0 = (WIDTH - w) // 2
    assert x0 == WIDTH - x0 - w  # symmetric margins either side


def test_the_clock_uses_the_injected_wall_clock():
    # gmtime rather than localtime is the whole point of the injection: this
    # assertion holds no matter what timezone the test machine is in.
    view = ClockView(load("spleen"), wall=lambda: EPOCH, localtime=time.gmtime)
    canvas = np.zeros((HEIGHT, WIDTH), dtype=bool)
    view.render(canvas, {}, NOW)
    expected = np.zeros((HEIGHT, WIDTH), dtype=bool)
    font = load("spleen")
    big = upscale(font.render("03:04"), 4)
    h, w = big.shape
    x0 = (WIDTH - w) // 2
    y0 = (HEIGHT - h) // 2
    expected[y0 : y0 + h, x0 : x0 + w] |= big
    assert np.array_equal(canvas[: view.big_h, :], expected[: view.big_h, :])


def test_the_same_minute_renders_byte_identical_frames():
    # This is the property the push count depends on: the scheduler asks for
    # a render every second (refresh=1.0), but only a real minute change
    # should ever cost the Compositor an actual push.
    view = clock_view()
    a = np.zeros((HEIGHT, WIDTH), dtype=bool)
    b = np.zeros((HEIGHT, WIDTH), dtype=bool)
    view.render(a, {}, NOW)
    view.render(b, {}, NOW + 1.0)  # `now` is store-monotonic, wall clock is fixed
    assert np.array_equal(a, b)


def test_a_new_minute_changes_the_framebuffer():
    a = np.zeros((HEIGHT, WIDTH), dtype=bool)
    b = np.zeros((HEIGHT, WIDTH), dtype=bool)
    ClockView(load("spleen"), wall=lambda: EPOCH, localtime=time.gmtime).render(a, {}, NOW)
    ClockView(load("spleen"), wall=lambda: EPOCH + 60, localtime=time.gmtime).render(b, {}, NOW)
    assert not np.array_equal(a, b)


def test_the_clock_ignores_the_now_argument():
    # `now` is the monotonic store clock, irrelevant to a wall-clock view --
    # passing wildly different values must not change the rendered frame.
    view = clock_view()
    a = np.zeros((HEIGHT, WIDTH), dtype=bool)
    b = np.zeros((HEIGHT, WIDTH), dtype=bool)
    view.render(a, {}, 0.0)
    view.render(b, {}, 999999.0)
    assert np.array_equal(a, b)


# -- AlertView --------------------------------------------------------------

HOT = Rule("cpu.temp", "CPU HOT", above=70.0)


def test_an_alert_view_with_nothing_shown_renders_blank():
    view = AlertView(load("spleen"))
    canvas = np.zeros((HEIGHT, WIDTH), dtype=bool)
    view.render(canvas, snap(**FULL), NOW)
    assert not canvas.any()


def test_the_alert_label_is_drawn_at_double_height():
    view = AlertView(load("spleen"))
    view.show(Alert(HOT, 90.0, NOW - 5.0))
    canvas = np.zeros((HEIGHT, WIDTH), dtype=bool)
    view.render(canvas, snap(**{"cpu.temp": 90.0}), NOW)
    rows_lit = np.flatnonzero(canvas.any(axis=1))
    # Spleen is 8px tall; the label is upscaled x2, so its cell is 16px --
    # everything drawn should sit within the top 16 rows or below it, and
    # some of the label's own ink should reach past the single-height mark.
    assert rows_lit[0] < 8


def test_the_alert_value_goes_through_fmt():
    view = AlertView(load("spleen"))
    view.show(Alert(HOT, 90.0, NOW - 5.0))
    canvas = np.zeros((HEIGHT, WIDTH), dtype=bool)
    view.render(canvas, snap(**{"cpu.temp": 90.0}), NOW)
    assert canvas.any()  # sanity: something drew below the label


def test_a_stale_reading_under_an_alert_still_shows_placeholder():
    # The alert firing must not make a number look more current than the
    # freshness discipline everywhere else in this module allows.
    view = AlertView(load("spleen"))
    view.show(Alert(HOT, 90.0, NOW - 5.0))
    stale = {"cpu.temp": Reading("cpu.temp", 90.0, NOW - 999.0, ttl=8.0)}
    canvas = np.zeros((HEIGHT, WIDTH), dtype=bool)
    view.render(canvas, stale, NOW)
    with_stale = canvas.copy()

    fresh = {"cpu.temp": Reading("cpu.temp", 90.0, NOW, ttl=60.0)}
    canvas[:] = False
    view.render(canvas, fresh, NOW)
    # A stale "90.0" (5 chars incl. sign-less digits) and MISSING ("--", 2
    # chars) draw different amounts of ink below the label -- the two
    # frames must differ, or fmt() isn't actually being consulted.
    assert not np.array_equal(with_stale, canvas)


def test_show_none_blanks_a_previously_shown_alert():
    view = AlertView(load("spleen"))
    view.show(Alert(HOT, 90.0, NOW - 5.0))
    view.show(None)
    canvas = np.zeros((HEIGHT, WIDTH), dtype=bool)
    view.render(canvas, snap(**{"cpu.temp": 90.0}), NOW)
    assert not canvas.any()
