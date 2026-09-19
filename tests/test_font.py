"""Vendored bitmap fonts: glyph data, layout, clipping, fallback."""

import numpy as np
import pytest

from oled_hud.hud.font import Font, load, names, upscale

# Golden bitmaps, transcribed from the BDF sources by eye. These are the
# check that scripts/build_fonts.py placed glyphs against the baseline
# correctly -- a builder that ignored BBX offsets would still produce
# plausible-looking output, just with every descender flattened onto the
# baseline, which only a per-row comparison catches.
SPLEEN_A = [
    ".....",
    ".##..",
    "#..#.",
    "#..#.",
    "####.",
    "#..#.",
    "#..#.",
    ".....",
]
SPLEEN_DOT = [
    ".....",
    ".....",
    ".....",
    ".....",
    ".....",
    ".....",
    "..#..",
    ".....",
]
TOMTHUMB_A = [
    ".#..",
    "#.#.",
    "###.",
    "#.#.",
    "#.#.",
    "....",
]
TOMTHUMB_G = [
    "....",
    ".##.",
    "#.#.",
    "###.",
    "..#.",
    ".#..",
]


def as_bits(art):
    return np.array([[c == "#" for c in row] for row in art], dtype=bool)


def glyph(font, ch):
    return font.glyphs[ord(ch) - font.first]


@pytest.fixture
def spleen():
    return load("spleen")


@pytest.fixture
def tomthumb():
    return load("tomthumb")


def test_all_fonts_are_registered():
    assert names() == ["fixed4x6", "spleen", "tomthumb"]


def test_cell_sizes(spleen, tomthumb):
    assert (spleen.width, spleen.height) == (5, 8)
    assert (tomthumb.width, tomthumb.height) == (4, 6)


def test_full_ascii_range_is_present(spleen, tomthumb):
    for font in (spleen, tomthumb, load("fixed4x6")):
        assert font.first == 32
        assert font.count == 95  # 32..126 inclusive
        assert font.glyphs.shape == (95, font.height, font.width)


def test_spleen_capital_a_matches_golden(spleen):
    assert np.array_equal(glyph(spleen, "A"), as_bits(SPLEEN_A))


def test_spleen_period_sits_on_the_baseline(spleen):
    assert np.array_equal(glyph(spleen, "."), as_bits(SPLEEN_DOT))


def test_tomthumb_capital_a_matches_golden(tomthumb):
    assert np.array_equal(glyph(tomthumb, "A"), as_bits(TOMTHUMB_A))


def test_tomthumb_descender_drops_below_the_baseline(tomthumb):
    # 'g' must occupy the bottom row that 'A' leaves blank -- this is the
    # BBX y-offset being honored rather than every glyph top-aligned.
    assert np.array_equal(glyph(tomthumb, "g"), as_bits(TOMTHUMB_G))
    assert glyph(tomthumb, "g")[-1].any()
    assert not glyph(tomthumb, "A")[-1].any()


def test_space_is_blank_and_hash_is_not(spleen, tomthumb):
    for font in (spleen, tomthumb):
        assert not glyph(font, " ").any()
        assert glyph(font, "#").any()


def test_no_glyph_in_the_range_is_accidentally_blank(spleen, tomthumb):
    for font in (spleen, tomthumb):
        blank = [
            chr(font.first + i)
            for i in range(font.count)
            if not font.glyphs[i].any() and chr(font.first + i) != " "
        ]
        assert blank == []


def test_render_shape_and_advance(spleen):
    bits = spleen.render("abc")
    assert bits.shape == (spleen.height, 3 * spleen.advance)
    assert spleen.measure("abc") == 15


def test_render_empty_string(spleen):
    assert spleen.render("").shape == (spleen.height, 0)


def test_render_concatenates_glyphs_in_order(spleen):
    bits = spleen.render("Ax")
    assert np.array_equal(bits[:, : spleen.advance], glyph(spleen, "A"))
    assert np.array_equal(bits[:, spleen.advance :], glyph(spleen, "x"))


def test_out_of_range_codepoints_fall_back_to_question_mark(spleen):
    # A producer handing a view a non-ASCII byte must not raise mid-frame.
    assert np.array_equal(spleen.render("é"), glyph(spleen, "?"))
    assert np.array_equal(spleen.render("\x01"), glyph(spleen, "?"))


def test_draw_places_text_at_the_given_offset(spleen):
    canvas = np.zeros((32, 128), dtype=bool)
    spleen.draw(canvas, "A", 10, 16)
    assert np.array_equal(canvas[16 : 16 + spleen.height, 10 : 10 + spleen.width], as_bits(SPLEEN_A))
    canvas[16 : 16 + spleen.height, 10 : 10 + spleen.width] = False
    assert not canvas.any()  # nothing drawn anywhere else


def test_draw_ors_rather_than_overwrites(spleen):
    canvas = np.zeros((32, 128), dtype=bool)
    canvas[0, 0] = True
    spleen.draw(canvas, " ", 0, 0)  # blank glyph must not erase what is there
    assert canvas[0, 0]


def test_draw_clips_at_the_right_edge(spleen):
    canvas = np.zeros((32, 128), dtype=bool)
    spleen.draw(canvas, "M" * 60, 0, 0)  # 300px of text into a 128px canvas
    assert canvas.shape == (32, 128)
    assert canvas[:, -1].any() or canvas[:, -2].any()


def test_draw_clips_partially_offscreen_left_and_bottom(spleen):
    canvas = np.zeros((32, 128), dtype=bool)
    spleen.draw(canvas, "A", -2, 28)  # half off the left, half below
    assert canvas.any()


def test_draw_entirely_offscreen_is_a_no_op(spleen):
    canvas = np.zeros((32, 128), dtype=bool)
    spleen.draw(canvas, "A", -50, 0)
    spleen.draw(canvas, "A", 200, 0)
    spleen.draw(canvas, "A", 0, 40)
    spleen.draw(canvas, "A", 0, -20)
    assert not canvas.any()


def test_load_is_cached_and_unknown_names_raise():
    assert load("spleen") is load("spleen")
    with pytest.raises(ValueError):
        load("comic-sans")


def test_glyph_array_is_not_shared_mutable_state(spleen):
    # Font instances are shared by load(); a view must not be able to
    # scribble on the glyph data by accident.
    before = spleen.glyphs.copy()
    spleen.render("hello")
    assert np.array_equal(spleen.glyphs, before)


def test_isolated_font_instances_are_independent():
    from oled_hud.hud.fonts import spleen as mod

    a, b = Font(mod), Font(mod)
    assert a.glyphs is not b.glyphs
    assert np.array_equal(a.glyphs, b.glyphs)


# -- upscale() ----------------------------------------------------------

def test_upscale_repeats_each_pixel_into_a_block():
    bits = np.array([[True, False], [False, True]])
    out = upscale(bits, 2)
    expected = np.array([
        [True, True, False, False],
        [True, True, False, False],
        [False, False, True, True],
        [False, False, True, True],
    ])
    assert np.array_equal(out, expected)


def test_upscale_preserves_shape_ratio():
    bits = np.zeros((8, 5), dtype=bool)
    assert upscale(bits, 4).shape == (32, 20)


def test_upscale_supports_independent_x_and_y_factors():
    bits = np.zeros((8, 5), dtype=bool)
    assert upscale(bits, 3, 2).shape == (16, 15)


def test_upscale_by_one_is_the_identity():
    bits = np.array([[True, False], [False, True]])
    assert np.array_equal(upscale(bits, 1), bits)


def test_upscale_of_an_empty_render_is_empty():
    bits = np.zeros((8, 0), dtype=bool)
    assert upscale(bits, 4).shape == (32, 0)


def test_spleen_at_four_x_fills_the_panel_height(spleen):
    # This is the number ClockView's layout depends on: 8px cell x4 == the
    # full 32px panel height, so a big clock needs no separate vertical fit.
    bits = spleen.render("A")
    assert upscale(bits, 4).shape[0] == 32


def test_a_five_character_clock_fits_the_width(spleen):
    # "12:34" at x4 must fit the 128px panel with room for a gutter.
    width = upscale(spleen.render("12:34"), 4).shape[1]
    assert width <= 128
