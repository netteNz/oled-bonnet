# oled

SSD1305 OLED driver + animation-engine work for a Raspberry Pi.

## Hardware

- Raspberry Pi 3 B+
- SSD1305 128x32 OLED, I2C (SCL/SDA), reset on `board.D4`

## Setup

Dependencies live in the `.env` virtualenv (not `python-dotenv` — an actual
venv with `board`/`busio`/`digitalio`/`adafruit_ssd1305`/`numpy`/`Pillow`
installed). Run scripts through it:

```
.env/bin/python3 test.py
.env/bin/python3 bench.py
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

## Status

This is Phase 0 (timing benchmarks) and Phase 3 (partial-blit driver) of an
animation-engine plan — FPS and scroll-step choices for later phases are
meant to come from `BENCH.md`'s measurements, not from assumptions.
