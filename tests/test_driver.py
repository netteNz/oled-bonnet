"""Tests for PartialSSD1305.blit()'s addressing math.

The one piece of the frame path that had no test: blit() computes a GDDRAM
window by hand, and every way it can be wrong is silent from Python's side
-- a bad column offset or a mis-sliced page just draws in the wrong place on
a panel nobody is looking at. NOTES.md records the two non-obvious facts it
has to get right: `_column_offset` defaults to 4 on this controller (so the
real column window is 4..131, not 0..127), and `show()` shifts 64-wide
panels by a further 32.

No hardware and no adafruit_ssd1305 __init__: the instance is built with
`object.__new__` and given only the five attributes blit() actually reads,
the same way tests/test_compositor.py fakes a driver rather than a panel.
"""

import numpy as np
import pytest
from adafruit_ssd1305 import SET_COL_ADDR, SET_PAGE_ADDR

from oled_hud.driver import PartialSSD1305
from oled_hud.pack import pack_bits
from tests.reference import reference_pack

COL_OFFSET = 4  # _SSD1305.__init__'s default; see NOTES.md


class FakeI2CDevice:
    """Records the payload instead of writing it, as a context manager."""

    def __init__(self):
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, payload):
        self.writes.append(bytes(payload))


def make_display(width=128, height=32, column_offset=COL_OFFSET):
    """A PartialSSD1305 with no I2C behind it.

    `object.__new__` skips SSD1305_I2C.__init__, which would open a real bus;
    blit() only reads the attributes set below plus write_cmd/i2c_device.
    """
    display = object.__new__(PartialSSD1305)
    display.width = width
    display.height = height
    display._column_offset = column_offset
    display.buffer = bytearray(1 + width * (height // 8))
    display.buffer[0] = 0x40
    display.cmds = []
    display.write_cmd = display.cmds.append
    display.i2c_device = FakeI2CDevice()
    return display


@pytest.fixture
def display():
    return make_display()


def test_window_commands_carry_the_column_offset(display):
    display.blit(bytes(10), col=20, ncols=10, page0=1, page1=1)
    assert display.cmds == [
        SET_COL_ADDR, 20 + COL_OFFSET, 29 + COL_OFFSET,
        SET_PAGE_ADDR, 1, 1,
    ]


def test_column_offset_is_read_from_the_instance_not_hardcoded():
    """NOTES.md: blit() must follow _column_offset if that default changes."""
    display = make_display(column_offset=0)
    display.blit(bytes(4), col=8, ncols=4, page0=0, page1=0)
    assert display.cmds[1:3] == [8, 11]


def test_payload_is_the_0x40_prefix_then_exactly_the_data(display):
    data = bytes(range(16))
    display.blit(data, col=0, ncols=16, page0=2, page1=2)
    assert display.i2c_device.writes == [bytes([0x40]) + data]


def test_multi_page_window_spans_the_page_range(display):
    display.blit(bytes(3 * 8), col=100, ncols=8, page0=1, page1=3)
    assert display.cmds == [
        SET_COL_ADDR, 100 + COL_OFFSET, 107 + COL_OFFSET,
        SET_PAGE_ADDR, 1, 3,
    ]
    assert len(display.i2c_device.writes[0]) == 1 + 24


def test_buffer_mirror_is_page_major_at_the_right_offsets(display):
    """`data` is page0's ncols bytes, then page1's -- each landing at
    1 + page*width + col so a later full show() agrees with the panel."""
    data = bytes([0xAA] * 4 + [0xBB] * 4)
    display.blit(data, col=12, ncols=4, page0=1, page1=2)

    assert bytes(display.buffer[1 + 1 * 128 + 12 : 1 + 1 * 128 + 16]) == b"\xaa" * 4
    assert bytes(display.buffer[1 + 2 * 128 + 12 : 1 + 2 * 128 + 16]) == b"\xbb" * 4
    # nothing outside the rectangle moved
    assert display.buffer[1 + 1 * 128 + 11] == 0
    assert display.buffer[1 + 1 * 128 + 16] == 0
    assert display.buffer[1 + 0 * 128 + 12] == 0
    assert display.buffer[1 + 3 * 128 + 12] == 0
    assert display.buffer[0] == 0x40  # the framing byte survives


def test_64_wide_panel_shifts_both_column_bounds_by_32():
    """Mirrors the width == 64 branch in _SSD1305.show()."""
    display = make_display(width=64)
    display.blit(bytes(5), col=10, ncols=5, page0=0, page1=0)
    assert display.cmds[1:3] == [10 + 32 + COL_OFFSET, 14 + 32 + COL_OFFSET]


def test_full_frame_blit_covers_the_whole_window(display):
    display.blit(bytes(4 * 128), col=0, ncols=128, page0=0, page1=3)
    assert display.cmds[1:3] == [COL_OFFSET, 127 + COL_OFFSET]
    assert len(display.i2c_device.writes[0]) == 1 + 512


@pytest.mark.parametrize(
    "data_len, ncols, page0, page1",
    [
        (9, 10, 0, 0),    # one byte short
        (11, 10, 0, 0),   # one byte long
        (10, 10, 0, 1),   # right for one page, half of what two need
        (0, 4, 0, 0),     # empty
    ],
)
def test_wrong_length_data_raises_before_touching_the_bus(
    display, data_len, ncols, page0, page1
):
    with pytest.raises(ValueError) as excinfo:
        display.blit(bytes(data_len), col=0, ncols=ncols, page0=page0, page1=page1)

    expected = ncols * (page1 - page0 + 1)
    assert str(expected) in str(excinfo.value)
    assert str(data_len) in str(excinfo.value)
    # the window must not have been programmed, and nothing written
    assert display.cmds == []
    assert display.i2c_device.writes == []


def test_packed_pixels_survive_the_round_trip(display):
    """End-to-end against the hardware-validated oracle: what pack_bits
    produces is what lands in the payload and in the buffer mirror."""
    rng = np.random.default_rng(0)
    bits = rng.random((16, 32)) > 0.5
    data = pack_bits(bits).tobytes()  # (pages, w) uint8 -> page-major bytes
    assert data == reference_pack(bits)

    display.blit(data, col=40, ncols=32, page0=0, page1=1)

    assert display.i2c_device.writes[0][1:] == data
    assert bytes(display.buffer[1 + 40 : 1 + 72]) == data[:32]
    assert bytes(display.buffer[1 + 128 + 40 : 1 + 128 + 72]) == data[32:]
