# oled

SSD1305 OLED driver + animation-engine work for a Raspberry Pi.

## Hardware

- Raspberry Pi 3 B+
- SSD1305 128x32 OLED, I2C (SCL/SDA), reset on `board.D4`

## Setup

Dependencies live in the `.env` virtualenv (not `python-dotenv` — an actual
venv with `board`/`busio`/`digitalio`/`adafruit_ssd1305`/`numpy`/`Pillow`/
`pytest` installed). Run scripts through it:

```
.env/bin/python3 test.py
.env/bin/python3 bench.py
.env/bin/python3 walk.py
.env/bin/python3 -m oled_hud.demos.tape_scroll
.env/bin/python3 -m pytest tests/
```

To run at 1MHz I2C instead of the Pi's 100kHz default, add
`dtparam=i2c_arm_baudrate=1000000` to `/boot/firmware/config.txt` and reboot.

## Layout

- `test.py` — smoke test: draws two lines of text and pushes them to the
  display.
- `oled_hud/driver.py` — `PartialSSD1305`, extends Adafruit's `SSD1305_I2C`
  with `blit(data, col, ncols, page0, page1)` to push a page/column
  rectangle instead of a full frame.
- `bench.py` — I2C timing harness. Benchmarks a full 512-byte frame (300
  samples), plus a 256-byte partial blit once `oled_hud.driver` is
  importable. Results are merged into `BENCH.md`, keyed by I2C clock speed
  and case — rerunning the same case adds a numbered run to its section
  instead of duplicating it.
- `BENCH.md` — recorded timing results; see it for current numbers.
- `walk.py` — stick-figure sprite that bounces back and forth across the
  display via `PartialSSD1305.blit()`; also the source of the
  hardware-validated packer used as a test oracle in `tests/reference.py`.
- `oled_hud/pack.py` — `pack_bits()`/`pack_image()`: MVLSB packing (numpy
  `packbits`, `bitorder="little"`) from a boolean pixel array or PIL image.
- `oled_hud/tape.py` — `Tape`: rasterizes a PIL image once, then hands out
  ready-to-blit 128px-wide frames via `frame(x)` with no PIL calls in the
  hot path — the packed-tape core a scrolling ticker is built on.
- `oled_hud/demos/tape_scroll.py` — demo: scrolls a text ticker on pages 2-3
  via `Tape.frame()` -> `blit()`.
- `tests/` — `pytest` suite for `oled_hud/pack.py`/`oled_hud/tape.py`,
  checked byte-for-byte against `tests/reference.py`'s hardware-validated
  packer.

## Status

Phases 0 (timing benchmarks), 1 (driver probe, partial), 2 (packed-tape
core), and 3 (partial-blit driver) of the animation-engine plan are done —
see `PROGRESS.md` for the phase/session tracker and `NOTES.md` for the
Phase 1 driver notes. Phase 4 (effects & scheduling) is next. FPS and
scroll-step choices are meant to come from `BENCH.md`'s measurements, not
from assumptions.
