# oled

## Purpose

Drives a 128x32 SSD1305 OLED bonnet on a Raspberry Pi: a partial-blit
driver, a no-PIL animation engine built on it, and a system-monitor HUD
daemon built on top of that. See `BENCH.md`/`PROGRESS.md` for why avoiding
full-frame pushes and PIL in the hot path mattered enough to build around.

## Features

- **Driver** — partial-blit `PartialSSD1305` on top of Adafruit's
  `SSD1305_I2C`, pushing only the dirty page/column rectangle.
- **Animation engine** — packed-tape scrolling, a fixed-timestep frame
  clock, non-blocking command-register effects (fade/blink/flash/invert),
  and a soak-test harness for long-run leak/latency verdicts.
- **HUD daemon** — diffing compositor (pushes only what changed), a
  TTL'd latest-value store fed by system producers (CPU/mem/disk/load/
  uptime/IP), vendored bitmap fonts, a scheduler rotating over several
  views with a horizontal slide and threshold-alert preemption, and a
  lifecycle-managed entrypoint (singleton lock, clean shutdown) with a
  `systemd` unit template.
- **Demos** — scrolling ticker, bouncing-sprite/wireframe animations, a
  live FFT audio spectrum visualizer, an ITU-R BS.1770-4 LUFS loudness
  meter, and two live data tickers (Prometheus/node_exporter telemetry,
  Coinbase BTC-USD price/holding).
- **Tests** — a fast, hardware-free `pytest` suite covering the packer,
  clock/effects/soak logic, driver GDDRAM math, and every HUD daemon
  module.

## How to Use

**Hardware:** Raspberry Pi 3 B+, SSD1305 128x32 OLED, I2C (SCL/SDA),
reset on `board.D4`.

### Setup

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
.env/bin/python3 -m oled_hud.demos.audio_visualizer --device 1 --floor -80
.env/bin/python3 -m oled_hud.demos.lufs_meter --device 1 --floor -90 --ceil -50

# telemetry / market data
.env/bin/python3 scripts/prom_pull.py --url http://<host>:9090 --interval 2
.env/bin/python3 -m oled_hud.demos.telemetry_display --url http://<host>:9090
.env/bin/python3 -m oled_hud.demos.coinbase_ticker --poll-interval 15
.env/bin/python3 -m oled_hud.demos.btc_sparkline --window 24h
.env/bin/python3 -m oled_hud.demos.btc_sparkline --window live --live-span 300
.env/bin/python3 -m oled_hud.demos.idle_clock
.env/bin/python3 -m oled_hud.demos.idle_clock --12h

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
mic to pick it up from the air. Both demos take `--floor`/`--ceil` (dB) to
tune the display range for a weak input chain — a phone's headphone-out
through a USB-C-to-3.5mm cable into a mic-in never reaches line level, so
the default range can sit close to the floor. `audio_visualizer.py`'s
default range worked fine as measured against that exact chain (rasp3,
session 12): the actual per-band FFT magnitude, with the frequency tilt
applied, swung from about -10 to +23dB, comfortably inside the default
-60/0 range, even though the raw time-domain level was only around -59dBFS.

**Capture hardware, verified on rasp3 (session 12):** the onboard 3.5mm jack
is output-only (`bcm2835 Headphones` reports `0 in, 8 out` — there's no way
to make it a mic input). A USB audio adapter/headset (the setup above) is
the default. Two no-USB alternatives have their *prerequisites* confirmed
present but are **not tested end-to-end** — no BT audio sink/source has
actually been verified through `sounddevice` yet, so treat both as
unverified until one is: a Bluetooth headset/mic, since the Pi 3B+'s
onboard radio (`hci0`, `bluetoothctl`) and PipeWire's Bluetooth backend
(`libspa-0.2-bluetooth`) are both already present — pairing one and
selecting its HSP/HFP (mic) profile is *expected* to expose it as a
`sounddevice` capture device with no code changes, but PortAudio's ALSA
backend may need pointing at a `pulse`/`pipewire` virtual device rather
than a raw `hw:X,Y` card the way the HyperX shows up, and that path hasn't
been exercised; and an I2S MEMS mic (INMP441/SPH0645-class, a few dollars),
since this rig only uses SCL/SDA/`board.D4` for the OLED, leaving GPIO18-21
free, and `/boot/firmware/config.txt` already has `#dtparam=i2s=on`
present, just commented out — this one is unverified even further, no
hardware has been connected.

`scripts/prom_pull.py` and `oled_hud/demos/telemetry_display.py` need a
reachable Prometheus/node_exporter (`--url`, default
`http://192.168.50.249:9090` — the user's Pi 4). Both are stdlib-only
(`urllib`), no extra dependency.

`oled_hud/demos/coinbase_ticker.py` and `oled_hud/demos/btc_sparkline.py`
need a Coinbase Developer Platform (CDP) API key and read it from
`.env.secrets` in the repo root — gitignored,
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
  startup, clean `SIGTERM` shutdown. Phase H2 (content): drives a `Store`, a
  `ProducerThread` and a `Scheduler` over several views (`run_loop()`, pulled
  out of `main()` so it's drivable against a fake driver with no hardware).
  `--views sys,clock` picks the rotation order, `--dwell` how long each view
  holds, `--transition {slide,none}`/`--transition-s` the switch between
  them, `--no-alerts` disables threshold preemption. `--font` picks the
  bitmap font and with it the line/column budget, `--fps` the frame loop
  rate, `--lock-path` where the singleton `flock` lives, and `--stats` prints
  the render/push counts on exit. No PIL anywhere in the loop. Entrypoint for
  `systemd/oled-hud.service`; see `PROGRESS.md`'s "HUD daemon" section for
  what's verified vs. what still needs the unit installed.
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
  smaller face. `upscale()` (Phase H2) integer-repeats a rendered bitmap's
  pixels for large type — `ClockView`'s big digits — without vendoring a
  second, bigger font.
- `scripts/build_fonts.py` + `fonts/` — offline BDF-to-module builder and
  the vendored BDF sources it reads. Run by hand, never imported at runtime;
  deterministic, so a rebuild leaves the generated modules byte-identical.
  See `fonts/NOTICE.md` for provenance and licensing.
- `oled_hud/hud/views.py` — `View` base (Phase H2): `name`, `refresh`
  (seconds between forced re-renders for a view whose pixels depend on wall
  time rather than any `Reading`) and a shared `render_into()`. Every view
  turns a `Store` snapshot into pixels and nothing else — no timer, no
  polling, no panel of its own, so the H2 scheduler can decide when it's
  drawn without the view having an opinion. `SysView` (Phase H1): `row()`
  packs several fields per line (first flush left, last flush right, middles
  evenly spaced), which is what lets the readable 4-line font carry the same
  content as a 5-line one instead of leaving a dozen dead columns in the
  middle of every row; a fifth line, when the font affords one, gets the 5-
  and 15-minute load averages. `ClockView` (Phase H2): big type via
  `font.upscale()` rather than a second vendored font — see
  `oled_hud/hud/font.py` below — reading injected wall time (`time.time`),
  not the monotonic clock everything else in this package is built on.
  `AlertView` (Phase H2): the rule and value an `Alerts` evaluation is
  currently firing, set via `show()` rather than through `render()`'s
  signature so it still satisfies the plain `View` contract.
- `oled_hud/hud/scheduler.py` — Phase H2: `Scheduler.frame()` decides,
  every frame, whether a redraw is owed — a `store.version` change (the H1
  case), a view's own `refresh` cadence, a dwell timer expiring (rotation)
  or a threshold alert appearing/clearing (preemption) — and is what the
  daemon's frame loop calls instead of the old `version != seen` check.
  Alert lookups read individual store keys with `store.get()`; a full
  `store.snapshot()` only happens on a frame that actually renders.
- `oled_hud/hud/alerts.py` — Phase H2: `Rule`/`Alerts` evaluate threshold
  conditions (with hysteresis and a `for_s` hold timer) against a `Store`
  snapshot on the render thread — deliberately not a `Producer`, since a
  producer's poll-and-forget shape has nowhere to keep that state and
  evaluating through the store would recreate the render-gate problem H2
  exists to solve one layer down. A stale reading never fires, matching
  `views.fmt()`'s freshness discipline.
- `oled_hud/hud/transition.py` — Phase H2: a horizontal slide between two
  packed `(PAGES, WIDTH)` framebuffers — pure column indexing (`pack_bits`
  is page-major MVLSB, so sliding sideways needs no unpacking), no PIL,
  time-driven like `effects.Effect`. `Slide.retarget()` swaps the incoming
  frame mid-transition without restarting progress, so a producer poll that
  lands mid-slide doesn't ship a frame that's stale by the slide's duration.
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
- `oled_hud/demos/coinbase_ticker.py` — live price and holding value for
  each asset in its `HOLDINGS` list (BTC, SOL) through the Compositor, one
  row per holding, same poll/frame-loop split as `telemetry_display.py`.
  Auth via the official `coinbase-advanced-py` SDK's `RESTClient`,
  credentials from `.env.secrets` (see Setup above).
- `oled_hud/demos/btc_sparkline.py` — BTC-USD trend sparkline (left 78px)
  next to a stacked span/price/change readout (right 48px), through the
  Compositor. `--window` picks the span: `24h`, `1h` and `30m` are candle
  windows refetched every `--refresh-interval`, while `live` is a sliding
  window (default 15m, `--live-span`) seeded from candles so it's full from
  the first frame, then carried forward by WS ticks placed on the x-axis by
  timestamp — points drift left in real time and age off the end. The plot
  auto-scales to the window's own min/max, floored at `MIN_RANGE_FRACTION`
  of the price so a quiet stretch doesn't get amplified into a fake crash.
  Same credentials as `coinbase_ticker.py`.
- `oled_hud/demos/idle_clock.py` — screensaver-style idle display: big
  seven-segment-style digits (hand-drawn rectangles, no PIL) with a
  blinking colon, plus a weekday/date line in the vendored Spleen font.
  `--12h` switches to a 12-hour clock with an AM/PM corner label. Redraws
  only on a minute boundary or a colon blink, same change-gated pattern as
  `coinbase_ticker.py`. The whole composition drifts a few pixels along a
  slow ~37-minute circular path so an idle screen doesn't park the same
  glyph shapes on the same OLED cells for hours — a stopgap for burn-in
  ahead of H4, which is still not started.

## Open Items

- **H4 (buttons, burn-in)** — not started. H0 (compositor), H1
  (store/producers/fonts/first view), H2 (views, scheduler, preemption,
  transitions) and H3 (daemon lifecycle) are done and soak-tested. H2 has
  one loose end: an eyeball verdict on x4 Spleen legibility for `ClockView`,
  which needs a human looking at the actual panel.
- **`systemd/oled-hud.service`** — written but not installed; needs a
  one-time `sudo` on the target Pi.
- **`hw_scroll_spike.py`** (hardware scroll register probe, noted in
  `NOTES.md`) — never run. Non-blocking: the tape approach avoids hardware
  scroll entirely, and nothing depends on it.

Everything else is implemented and validated: all five animation-engine
phases (0-4) are done and every acceptance criterion from the handoff is
checked off — a 30-minute production-config soak (60fps, 1px step) ran
108,000 frames with 0 late, 0 dropped, 0 errors, RSS flat, blit p95
unchanged start-to-end (`PROGRESS.md` session 5).

On the HUD side, the daemon shows live local-system telemetry and a big-type
clock, rotating between them with a horizontal slide, and preempts the
rotation for threshold alerts (CPU temp, disk, memory, load), all through
vendored bitmap fonts with no PIL in the frame path. The H1 baseline (one
static view, no transitions) measured 19 renders and 21 pushes over 1800
frames at 60fps — over 99% of frames pushed nothing. Rotating between two
views on an 8s dwell with a 0.3s slide spends some of that deliberately:
verified on the real panel at 30s/60fps, that config did 81 renders and 64
pushes over 1800 frames (62 pushing, 96.6% pushed nothing); dwell 30s or
`--transition none` lands within noise of the H1 number. All three ran with
0 late, 0 dropped, and 6.4-6.9ms of the 16.7ms budget to spare. See
`PROGRESS.md` session 11 for the full table, the forced-alert check and the
re-run H3 lifecycle checks.

See `PROGRESS.md` for the full phase/session tracker and `NOTES.md` for
the Phase 1 driver notes.
