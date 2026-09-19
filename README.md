# oled

SSD1305 OLED driver, animation engine, and a system-monitor HUD daemon
for a Raspberry Pi.

## Hardware

- Raspberry Pi 3 B+
- SSD1305 128x32 OLED, I2C (SCL/SDA), reset on `board.D4`

## Setup

Dependencies live in the `.env` virtualenv (not `python-dotenv` — an actual
venv with `board`/`busio`/`digitalio`/`adafruit_ssd1305`/`numpy`/`Pillow`/
`pytest` installed), pinned in `requirements.txt`:

```
python3 -m venv .env && .env/bin/pip install -r requirements.txt
```

Nothing here is installed as a package — the repo is run in place. Run
scripts through the venv:

```
# smoke test / benchmarks / tests
.env/bin/python3 test.py
.env/bin/python3 bench.py
.env/bin/python3 walk.py
.env/bin/python3 -m pytest tests/

# animation-engine demos
.env/bin/python3 -m oled_hud.demos.tape_scroll
.env/bin/python3 -m oled_hud.demos.ticker --fps 30 --seconds 60
.env/bin/python3 -m oled_hud.demos.ticker --fps 60 --step 1 --seconds 1800 \
    --soak-log runs/soak.jsonl
.env/bin/python3 scripts/analyze_soak.py runs/soak.jsonl --exit-code $?
.env/bin/python3 -m oled_hud.demos.hud_animate --seconds 20
.env/bin/python3 -m oled_hud.demos.wireframe --shape cube --seconds 20
.env/bin/python3 -m oled_hud.demos.font_sampler --font tomthumb
.env/bin/python3 scripts/build_fonts.py

# audio (needs sounddevice + a capture device, see below)
.env/bin/python3 -m oled_hud.demos.audio_visualizer --list
.env/bin/python3 -m oled_hud.demos.audio_visualizer --device 1 --bars 32
.env/bin/python3 -m oled_hud.demos.audio_visualizer --device 1 --style mirror
.env/bin/python3 -m oled_hud.demos.lufs_meter --device 1 --floor -90 --ceil -50

# telemetry / market data
.env/bin/python3 scripts/prom_pull.py --url http://<host>:9090 --interval 2
.env/bin/python3 -m oled_hud.demos.telemetry_display --url http://<host>:9090
.env/bin/python3 -m oled_hud.demos.coinbase_ticker --poll-interval 15

# HUD daemon (the main thing here)
.env/bin/python3 -m oled_hud.hud.daemon
.env/bin/python3 -m oled_hud.hud.daemon --font fixed4x6 --seconds 20 --stats
```

On the default font, the daemon shows:

```
+-------------------------+
|rasp3            up 5h38m|
|cpu 5%    53.7C   ld 0.37|
|mem 574/905M      dsk 31%|
|192.168.50.16       19.9G|
+-------------------------+
```

To run at 1MHz I2C instead of the Pi's 100kHz default, add
`dtparam=i2c_arm_baudrate=1000000` to `/boot/firmware/config.txt` and reboot.

`oled_hud/demos/audio_visualizer.py` and `oled_hud/demos/lufs_meter.py`
additionally need a capture device and `sounddevice`, neither part of the
base setup (`sounddevice` is installed in this venv, from session 7 — a
fresh one needs it): `sudo apt install -y portaudio19-dev` (system package,
needs `sudo`; provides the headers `sounddevice` builds against) then
`.env/bin/pip install sounddevice`. Run with `--list` first to see what
`sounddevice` detects and pick the right `--device`. A USB headset's mic
can't hear its own headphone output acoustically — if you want the
visualizer/meter reacting to something playing over headphones, route it
with a physical cable (headphone-out to mic-in) rather than relying on the
mic to pick it up from the air.

`scripts/prom_pull.py` and `oled_hud/demos/telemetry_display.py` need a
reachable Prometheus/node_exporter (`--url`, default
`http://192.168.50.249:9090` — the user's Pi 4). Both are stdlib-only
(`urllib`), no extra dependency.

`oled_hud/demos/coinbase_ticker.py` needs a Coinbase Developer Platform
(CDP) API key and reads it from `.env.secrets` in the repo root — gitignored,
`chmod 600`, **not** named `.env` since that's the venv directory (see
Setup above). Copy `.env.secrets.example` to `.env.secrets` and fill in your
own `CDP_API_KEY` and `CDP_API_SECRET`; never commit the real file. Auth
goes through the official `coinbase-advanced-py` SDK's `RESTClient` (in
`requirements.txt`), which signs the per-request JWT itself and works with
either an Ed25519 or an EC secret.

## Layout

### Core driver & engine

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

### Animation-engine demos

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

### Tests

- `tests/` — `pytest` suite for `pack.py`/`tape.py` (checked byte-for-byte
  against `tests/reference.py`'s hardware-validated packer), for
  `clock.py`/`effects.py` (driven by a fake clock and a fake display), for
  `soak.py`/`analyze_soak.py` (including the no-I/O-in-the-frame-path
  constraint and both leak-shape directions the analyzer has to tell apart),
  for `oled_hud/hud/compositor.py` (a fake driver records `blit()` calls),
  for `oled_hud/driver.py` (`test_driver.py` builds a `PartialSSD1305` with
  no I2C behind it and asserts the GDDRAM window math — the `+4` column
  offset, the 64-wide shift and the page-major buffer mirror — since every
  way that can be wrong is invisible from Python),
  and for the Phase H1 modules — `test_font.py` checks generated glyphs
  against golden bitmaps transcribed from the BDFs (the check that catches a
  builder ignoring BBX offsets and flattening every descender onto the
  baseline), `test_store.py` drives TTL staleness on an injected clock and
  hammers the store from eight threads, `test_producers.py` runs the parsers
  against captured `/proc` fixtures, and `test_views.py` asserts in a clean
  subprocess that the render path never imports PIL — all run fast and
  without hardware.

### HUD daemon

- `oled_hud/hud/compositor.py` — HUD daemon Phase H0: `Compositor` diffs a
  packed framebuffer against what's known to be on the panel and pushes
  only the minimum, choosing per dirty-page run between one wide push and
  several narrow ones by the `BENCH.md` push-cost model. `force_full()`
  invalidates the diff cache after a reset or burn-in offset change. See
  `PROGRESS.md`'s "HUD daemon" section for phase status.
- `oled_hud/demos/compositor_static.py` — demo: a static view through the
  Compositor, printing pushes-per-frame to show it settles to zero once
  nothing changes.
- `oled_hud/hud/daemon.py` — HUD daemon entrypoint. Phase H3 (lifecycle):
  singleton via a non-blocking `flock`, hardware reset + `force_full()` on
  startup, clean `SIGTERM` shutdown. Phase H1 (content): drives a `Store`, a
  `ProducerThread` and a `SysView`, re-rendering only when `store.version`
  changes so almost every frame stays in the Compositor's push-nothing path
  (measured: 19 renders and 21 pushes over 1800 frames at 60fps). `--font`
  picks the bitmap font and with it the line/column budget, `--fps` the frame
  loop rate, `--lock-path` where the singleton `flock` lives, and `--stats`
  prints the render/push counts on exit. No PIL anywhere in the loop.
  Entrypoint for `systemd/oled-hud.service`; see `PROGRESS.md`'s "HUD
  daemon" section for what's verified vs. what still needs the unit
  installed.
- `systemd/oled-hud.service` — user unit template for the daemon above.
- `oled_hud/hud/__init__.py` — the panel's geometry (`WIDTH`, `HEIGHT`, and
  `PAGES` derived from it), in one place because `views.py` thinks in pixels
  while `compositor.py` thinks in pages and the two have to agree.
- `oled_hud/hud/store.py` — HUD daemon Phase H1: a thread-safe latest-value
  store shared between the producer thread and the render loop. One value
  per key, `snapshot()` copies everything under one lock, and staleness is
  derived on read from each entry's TTL — a producer that dies stops
  refreshing its key and the value ages out to `--` on its own, instead of
  a frozen number that still looks live. A `version` counter is what the
  daemon's re-render gate watches.
- `oled_hud/hud/producers.py` — HUD daemon Phase H1: local-system producers
  (CPU temp, CPU usage from a `/proc/stat` delta, load, memory via
  `MemAvailable`, uptime, disk, hostname/IP) and the single thread that runs
  them on their own cadences. Parsers are separate functions taking text, so
  they're tested against captured `/proc` fixtures. A producer that raises
  is counted and rescheduled, never fatal.
- `oled_hud/hud/font.py` + `oled_hud/hud/fonts/` — HUD daemon Phase H1:
  bitmap text with numpy only, no PIL. `oled_hud/hud/fonts/*.py` are
  generated glyph data (base64-packed bits); rendering a string is one
  fancy-index plus a reshape. `load("spleen")` (5x8, 25x4),
  `load("tomthumb")` or `load("fixed4x6")` (both 4x6, 32x5). Spleen is the
  default: see `PROGRESS.md` session 9 for why the 4x6 fonts lost and why
  the answer to wanting more on screen turned out to be the layout, not a
  smaller face.
- `scripts/build_fonts.py` + `fonts/` — offline BDF-to-module builder and
  the vendored BDF sources it reads. Run by hand, never imported at runtime;
  deterministic, so a rebuild leaves the generated modules byte-identical.
  See `fonts/NOTICE.md` for provenance and licensing.
- `oled_hud/hud/views.py` — HUD daemon Phase H1: `SysView` turns a `Store`
  snapshot into pixels and nothing else — no timer, no polling, no panel, so
  H2's scheduler can decide when it's drawn without the view having an
  opinion. `row()` packs several fields per line (first flush left, last
  flush right, middles evenly spaced), which is what lets the readable
  4-line font carry the same content as a 5-line one instead of leaving a
  dozen dead columns in the middle of every row. Layout adapts to the font's
  budget; a fifth line, when the font affords one, gets the 5- and
  15-minute load averages.
- `oled_hud/demos/font_sampler.py` — puts any of the three vendored fonts'
  printable ASCII range (or `--text`) on the panel, so "25 readable columns
  or 32 cramped ones" is judged by eye rather than from the numbers.
- `oled_hud/demos/hud_animate.py` — playground demo: a bouncing square
  (pages 0-1) and a scrolling random-walk sparkline (pages 2-3) driven
  straight through the Compositor, no PIL, no Store/producers — exercises
  `plan_run()` against real per-frame motion rather than the static case.

### Audio demos

- `oled_hud/demos/audio_visualizer.py` — live FFT spectrum analyzer off a
  USB mic via `sounddevice`: 32 log-spaced bars, zero-padded FFT (finer
  bin spacing at the low end without added latency), a per-bar dB tilt to
  counter real audio's high-frequency roll-off, fast-attack/slow-release
  smoothing, drawn straight into the Compositor's `fb`. `--list` to see
  detected devices, `--device` to pick one. `--style {bars,mirror}` picks
  bottom-up bars or bars grown from the vertical center outward. `SIGTERM`
  clears the panel the same way Ctrl+C does. See `PROGRESS.md` session 7
  for the dead-low-bars bug and session 8 for the mirror style and a
  floating-mic-input noise investigation.
- `oled_hud/demos/lufs_meter.py` — ITU-R BS.1770-4 loudness meter off the
  same mic capture: a K-weighting filter (`Biquad`, two cascaded stages,
  coefficients derived per sample rate via the bilinear transform rather
  than hardcoded for 48kHz) feeds momentary (400ms) and short-term (3s)
  loudness, rendered as a scrolling LUFS history. `--floor`/`--ceil` tune
  the display range for a given input chain's actual level. Validated
  against BS.1770's own calibration point — see `PROGRESS.md` session 8.

### Other demos

- `oled_hud/demos/wireframe.py` — a rotating 3D wireframe cube or pyramid
  (`--shape`), no PIL: per-frame rotation matrix, perspective projection
  scaled to the panel's 32px height, edges rasterized as lines via
  `np.linspace`. `pyramid` is the default — fewer edges alias less at this
  resolution than the cube, per live feedback in `PROGRESS.md` session 8.

### Telemetry & market data

- `scripts/prom_pull.py` — standalone terminal script (stdlib `urllib`
  only) pulling CPU temp/load/usage from a remote Prometheus/node_exporter
  and printing them; `--interval` loops. No OLED involved — just the raw
  numbers, for checking a telemetry source before wiring it into anything.
- `oled_hud/demos/telemetry_display.py` — the same telemetry rendered as
  text through the real Compositor. Polling (`--poll-interval`) runs on
  its own cadence, decoupled from the frame loop (`--fps`) — a poll
  failure keeps showing the last good reading instead of crashing.
- `oled_hud/demos/coinbase_ticker.py` — live BTC-USD price and BTC holding
  through the Compositor, same poll/frame-loop split as
  `telemetry_display.py`. Auth via the official `coinbase-advanced-py`
  SDK's `RESTClient`, credentials from `.env.secrets` (see Setup above).

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

On the HUD side, phases H0 (compositor), H1 (store, producers, fonts, first
real view) and H3 (daemon lifecycle) are implemented. The daemon now shows
live local-system telemetry through vendored bitmap fonts with no PIL in the
frame path: a 30-second run at 60fps did 1800 frames with 0 late and 0
dropped, re-rendering 19 times and pushing 21 times — over 99% of frames
pushed nothing at all. H2 (view scheduler) and H4 (buttons, burn-in) are
next; the `systemd` unit is still uninstalled and needs a one-time `sudo`.

FPS and scroll-step choices come from `BENCH.md`'s measurements and
`FrameClock`'s reported slack, not from assumptions. Still open: the
Phase 1 `hw_scroll_spike.py` probe noted in `NOTES.md` — never blocking,
since the tape approach avoids hardware scroll entirely.
