"""SysView: layout per font, staleness rendering, and the no-PIL guarantee."""

import subprocess
import sys

import numpy as np
import pytest

from oled_hud.hud.font import load
from oled_hud.hud.producers import format_uptime
from oled_hud.hud.store import Reading, Store
from oled_hud.hud.views import HEIGHT, MISSING, WIDTH, SysView, fmt, row

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
        " oled_hud.pack, oled_hud.clock;"
        "print([m for m in sys.modules if m.split('.')[0] in ('PIL',)])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout
