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
.env/bin/python3 -m oled_hud.demos.ticker --fps 30 --seconds 60
.env/bin/python3 -m oled_hud.demos.ticker --fps 60 --step 1 --seconds 1800 \
    --soak-log runs/soak.jsonl
.env/bin/python3 scripts/analyze_soak.py runs/soak.jsonl --exit-code $?
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
- `oled_hud/clock.py` — `FrameClock`: fixed-timestep pacer. Deadlines come
  from a fixed origin, so a slow frame doesn't shift every later one; a bad
  overrun skips missed frames rather than bursting to catch up. Reports
  per-frame slack and a `summary()` of the measured frame budget.
- `oled_hud/effects.py` — command-register effects: `contrast`, `invert`,
  `all_on`, plus non-blocking `Fade`/`Blink`/`Flash` and an `EffectQueue`.
  Driven by elapsed time rather than frame count, so they keep wall-clock
  speed regardless of fps or dropped frames.
- `oled_hud/demos/tape_scroll.py` — demo: scrolls a text ticker on pages 2-3
  via `Tape.frame()` -> `blit()`, paced by `time.sleep()`.
- `oled_hud/demos/ticker.py` — the same ticker on `FrameClock` with an
  effect queue; takes `--fps`/`--seconds`/`--step`/`--text` and prints its
  measured slack on exit. `--soak-log PATH` additionally records per-minute
  JSONL buckets via `SoakRecorder` for a soak run; a `SIGTERM` handler
  takes the same clean-exit path as Ctrl+C so a `kill`/timeout doesn't lose
  the final bucket.
- `oled_hud/soak.py` — `SoakRecorder` (per-minute JSONL buckets: blit-time
  percentiles, slack, late/dropped counts, RSS, errors by type) and
  `BlitTimer` (times the blit call, counts consecutive I2C errors, raises
  only once a streak looks like a wedged bus rather than a transient).
- `scripts/analyze_soak.py` — reads a soak JSONL log and prints a pass/fail
  verdict: RSS slope (overall and tail-only, to tell a leak from a
  converged warm-up staircase), blit p95 drift between the first and last
  third of the run, error counts, and the worst-case slack floor.
- `tests/` — `pytest` suite for `pack.py`/`tape.py` (checked byte-for-byte
  against `tests/reference.py`'s hardware-validated packer), for
  `clock.py`/`effects.py` (driven by a fake clock and a fake display), for
  `soak.py`/`analyze_soak.py` (including the no-I/O-in-the-frame-path
  constraint and both leak-shape directions the analyzer has to tell apart),
  and for `oled_hud/hud/compositor.py` (a fake driver records `blit()`
  calls; no hardware) — all run fast and without hardware.
- `oled_hud/hud/compositor.py` — HUD daemon Phase H0: `Compositor` diffs a
  packed framebuffer against what's known to be on the panel and pushes
  only the minimum, choosing per dirty-page run between one wide push and
  several narrow ones by the `BENCH.md` push-cost model. `force_full()`
  invalidates the diff cache after a reset or burn-in offset change. See
  `PROGRESS.md`'s "HUD daemon" section for phase status.
- `oled_hud/demos/compositor_static.py` — demo: a static view through the
  Compositor, printing pushes-per-frame to show it settles to zero once
  nothing changes.

## Status

All five phases of the animation-engine plan are implemented: 0 (timing
benchmarks), 1 (driver probe, partial), 2 (packed-tape core), 3
(partial-blit driver) and 4 (effects & scheduling) — and every acceptance
criterion from the handoff is checked off. See `PROGRESS.md` for the
phase/session tracker and `NOTES.md` for the Phase 1 driver notes.

The 30-minute production-config soak (60fps, 1px step) ran clean:
108,000 frames, 0 late, 0 dropped, 0 errors, RSS flat after an early
warm-up staircase, blit p95 unchanged start-to-end. `py-spy` confirms the
frame loop never touches PIL/font code, not just by inspection — see
`PROGRESS.md`'s session 5 for the full numbers and the
`scripts/analyze_soak.py` methodology (including a real bug it caught in
its own first version: a naive "monotonic" leak check that couldn't tell
a plateaued warm-up staircase from an actual climb).

FPS and scroll-step choices come from `BENCH.md`'s measurements and
`FrameClock`'s reported slack, not from assumptions. Still open: the
Phase 1 `hw_scroll_spike.py` probe noted in `NOTES.md` — never blocking,
since the tape approach avoids hardware scroll entirely.
