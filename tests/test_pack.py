import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from oled_hud.pack import pack_bits
from oled_hud.tape import VIEW, Tape
from tests.reference import reference_pack
from walk import stick_figure


def _text_bits(text: str, height: int = 16) -> np.ndarray:
    img = Image.new("1", (len(text) * 8 + 4, height))
    draw = ImageDraw.Draw(img)
    draw.text((0, 0), text, fill=255, font=ImageFont.load_default())
    return np.asarray(img, dtype=np.uint8) > 0


def test_matches_reference_on_stick_figure_sprite():
    for phase in range(8):
        sprite = stick_figure(phase)
        bits = np.asarray(sprite, dtype=np.uint8) > 0
        assert pack_bits(bits).tobytes() == reference_pack(bits)


@pytest.mark.parametrize(
    "text",
    ["Hello, world!", "The quick brown fox jumps over the lazy dog", "0123456789"],
)
def test_matches_reference_on_text_lines(text):
    bits = _text_bits(text)
    assert pack_bits(bits).tobytes() == reference_pack(bits)


@pytest.mark.parametrize("height", [8, 16, 32])
def test_valid_heights_pack(height):
    bits = np.zeros((height, 10), dtype=bool)
    bits[0, 0] = True
    packed = pack_bits(bits)
    assert packed.shape == (height // 8, 10)
    assert packed.tobytes() == reference_pack(bits)


def test_non_multiple_of_8_height_raises():
    bits = np.zeros((10, 5), dtype=bool)
    with pytest.raises(ValueError):
        pack_bits(bits)


@pytest.mark.parametrize("seed", range(20))
def test_random_shapes_match_reference(seed):
    rng = np.random.default_rng(seed)
    height = int(rng.choice([8, 16, 24, 32]))
    width = int(rng.integers(1, 40))
    bits = rng.integers(0, 2, size=(height, width), dtype=np.uint8).astype(bool)
    assert pack_bits(bits).tobytes() == reference_pack(bits)


def test_two_page_ticker_pushes_256_bytes_per_frame():
    img = Image.new("1", (200, 16))
    draw = ImageDraw.Draw(img)
    draw.text((0, 0), "0123456789" * 6, fill=255, font=ImageFont.load_default())
    tape = Tape(img)
    assert tape.pages == 2
    assert len(tape.frame(0)) == tape.pages * VIEW == 256


def test_tape_shorter_than_viewport_raises():
    img = Image.new("1", (20, 8))
    with pytest.raises(ValueError):
        Tape(img, gap=4)
