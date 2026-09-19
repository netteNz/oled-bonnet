# Features

Every runnable thing in this repo, one line on what it does and the exact
command to run it. For hardware/venv setup see `README.md`'s Setup section;
for how any of this was built see `PROGRESS.md`.

Nothing here is installed as a package — run everything through the venv,
from the repo root:

```
.env/bin/python3 <script>.py
.env/bin/python3 -m oled_hud.<module>
```

Demos needing `Ctrl+C` to stop clear the panel on exit either way (SIGINT
and SIGTERM are both handled). A `--seconds 0` (the default on most) runs
until you stop it; a nonzero value runs exactly that long and exits on its
own — useful for a quick unattended check.

---

## The HUD daemon

The main thing here: a system-monitor HUD that rotates between views,
preempts itself for threshold alerts, and slides between whichever two
views are involved.

```
.env/bin/python3 -m oled_hud.hud.daemon
```

On the default font and views, it shows:

```
+-------------------------+
|rasp3            up 5h38m|
|cpu 5%    53.7C   ld 0.37|
|mem 574/905M      dsk 31%|
|192.168.50.16       19.9G|
+-------------------------+
```

rotating every 8s into a big-type clock, and switching to a CPU-hot / disk-full
/ memory / sustained-load banner if a threshold trips.

| flag | default | what |
|---|---|---|
| `--font {spleen,tomthumb,fixed4x6}` | `spleen` | bitmap font; sets the line/column budget |
| `--views sys,clock` | `sys,clock` | comma-separated rotation order |
| `--dwell SECONDS` | `8.0` | how long each view holds before rotating |
| `--transition {slide,none}` | `slide` | how the panel switches between views |
| `--transition-s SECONDS` | `0.3` | slide duration |
| `--no-alerts` | off | disable threshold-alert preemption entirely |
| `--fps HZ` | `60.0` | frame loop rate |
| `--lock-path PATH` | `~/.oled-hud.lock` | singleton flock — a second instance exits immediately rather than fighting the first for the I2C bus |
| `--seconds N` | `0` (until signalled) | run time |
| `--stats` | off | print render/push counts on exit |

Examples:

```
.env/bin/python3 -m oled_hud.hud.daemon --font fixed4x6 --seconds 20 --stats
.env/bin/python3 -m oled_hud.hud.daemon --dwell 30 --transition none   # one long-held view at a time, no slide cost
.env/bin/python3 -m oled_hud.hud.daemon --no-alerts                    # rotation only, no preemption
```

Threshold alerts (`oled_hud/hud/alerts.py`, tunable there, not on the CLI):
CPU temp > 70°C, disk > 90%, memory > 92% (held 10s), 1-minute load > 4.0
(held 30s) — each with hysteresis so a value oscillating near the threshold
doesn't flap the alert on and off.

To run at 1MHz I2C instead of the Pi's 100kHz default (a real frame-time
difference — see `BENCH.md`), add `dtparam=i2c_arm_baudrate=1000000` to
`/boot/firmware/config.txt` and reboot.

### Running it as a service

**Not yet installed on the Pi** (see `PROGRESS.md` — "Still waiting on the
user"); the two commands the handoff calls for are `sudo loginctl
enable-linger` and `systemctl --user enable --now`. The full sequence, standard
systemd user-unit mechanics around those two:

```
mkdir -p ~/.config/systemd/user
cp systemd/oled-hud.service ~/.config/systemd/user/
sudo loginctl enable-linger $USER      # let it run without a login session
systemctl --user daemon-reload
systemctl --user enable --now oled-hud.service
```

The unit assumes the repo lives at `~/Scripts/oled` (`ExecStart=%h/Scripts/oled/...`
in `systemd/oled-hud.service`) — edit that path first if yours is elsewhere.
Useful commands once installed: `systemctl --user status oled-hud`,
`journalctl --user -u oled-hud -f`, `systemctl --user restart oled-hud`.

---

## HUD building-block demos

Smaller pieces of the daemon's stack, run standalone — useful for judging
one thing (a font, the diff engine) without the rest of the HUD running.

**Static view / diff-engine sanity check** — one frame pushed, then nothing:

```
.env/bin/python3 -m oled_hud.demos.compositor_static
.env/bin/python3 -m oled_hud.demos.compositor_static --seconds 10 --text "hello"
```

**Moving content** — a bouncing square + scrolling sparkline, so the diff
engine is exercised against real per-frame motion instead of the static case:

```
.env/bin/python3 -m oled_hud.demos.hud_animate
.env/bin/python3 -m oled_hud.demos.hud_animate --seconds 20 --fps 60 --step 3
```

**Font sampler** — judge a vendored font by eye rather than by column count:

```
.env/bin/python3 -m oled_hud.demos.font_sampler --font spleen
.env/bin/python3 -m oled_hud.demos.font_sampler --font tomthumb --seconds 30
.env/bin/python3 -m oled_hud.demos.font_sampler --text "cpu 12%  49.9C"
```

---

## Data-source demos

Each of these is a full mini-HUD on its own — real data through the
Compositor, no Store/producer/scheduler scaffolding.

**Remote telemetry** (Prometheus/node_exporter) — needs a reachable server:

```
.env/bin/python3 -m oled_hud.demos.telemetry_display
.env/bin/python3 -m oled_hud.demos.telemetry_display --url http://192.168.50.249:9090
.env/bin/python3 -m oled_hud.demos.telemetry_display --poll-interval 2 --seconds 60
```

**Coinbase BTC-USD ticker + your BTC holding** — needs a CDP API key in
`.env.secrets` (copy `.env.secrets.example`, fill in `CDP_API_KEY` /
`CDP_API_SECRET`, `chmod 600`; gitignored, never commit the real file):

```
.env/bin/python3 -m oled_hud.demos.coinbase_ticker
.env/bin/python3 -m oled_hud.demos.coinbase_ticker --poll-interval 15
```

**Audio spectrum visualizer** — needs a capture device (`sudo apt install -y
portaudio19-dev` once, then `.env/bin/pip install sounddevice`):

```
.env/bin/python3 -m oled_hud.demos.audio_visualizer --list        # see devices first
.env/bin/python3 -m oled_hud.demos.audio_visualizer --device 1
.env/bin/python3 -m oled_hud.demos.audio_visualizer --device 1 --style mirror --bars 32
```

**LUFS loudness meter** — same audio setup as above, ITU-R BS.1770-4:

```
.env/bin/python3 -m oled_hud.demos.lufs_meter --list
.env/bin/python3 -m oled_hud.demos.lufs_meter --device 1 --floor -90 --ceil -50
```

**Rotating 3D wireframe** — no external dependency, just numpy:

```
.env/bin/python3 -m oled_hud.demos.wireframe
.env/bin/python3 -m oled_hud.demos.wireframe --shape cube --speed 2.0
.env/bin/python3 -m oled_hud.demos.wireframe --seconds 20
```

---

## Animation-engine demos (pre-HUD)

Earlier phases' demos — the packed-tape/effects layer the HUD is built on
top of, still runnable on their own.

**Text ticker** — scrolls pre-packed bytes, no PIL/font work in the loop:

```
.env/bin/python3 -m oled_hud.demos.tape_scroll
```

**Ticker with pacing + effects**, and an optional instrumented soak run:

```
.env/bin/python3 -m oled_hud.demos.ticker
.env/bin/python3 -m oled_hud.demos.ticker --fps 40 --seconds 60
.env/bin/python3 -m oled_hud.demos.ticker --fps 60 --step 1 --seconds 1800 \
    --soak-log runs/soak.jsonl
```

---

## Scripts

**Rebuild vendored fonts** — offline BDF → packed glyph module, run only
when a font source changes:

```
.env/bin/python3 scripts/build_fonts.py                 # rebuild both
.env/bin/python3 scripts/build_fonts.py --font spleen    # just one
```

**Pull Prometheus telemetry to the terminal** — no OLED, just the numbers,
for checking a source before wiring it in:

```
.env/bin/python3 scripts/prom_pull.py
.env/bin/python3 scripts/prom_pull.py --url http://192.168.50.249:9090 --interval 2
```

**Analyze a soak log** — RSS slope, blit p95 drift, error counts, worst-frame
slack, printed as a pass/fail verdict:

```
.env/bin/python3 scripts/analyze_soak.py runs/soak.jsonl
.env/bin/python3 scripts/analyze_soak.py runs/*.jsonl --exit-code 0
```

---

## Root-level one-offs

**`test.py`** — the original 2-line smoke test: draw text, show it.
**`bench.py`** — Phase 0 timing harness; results append to `BENCH.md`.
Run once at the default I2C clock, then again after the 1MHz `dtparam`
change above.
**`walk.py`** — a stick-figure sprite bouncing across the panel via
`PartialSSD1305.blit()`, pushing only the columns that changed.

```
.env/bin/python3 test.py
.env/bin/python3 bench.py
.env/bin/python3 walk.py
```

---

## Tests

```
.env/bin/python3 -m pytest tests/
```

Must run from the repo root (no `conftest.py`; a few tests import root-level
modules directly). Five modules need real hardware imports (`board`,
`busio`, `adafruit_ssd1305`, `fcntl`) and only run on the Pi:
`test_daemon.py`, `test_driver.py`, `test_effects.py`, `test_pack.py`,
`test_soak.py`. Everything else runs anywhere, including off-Pi:

```
.env/bin/python3 -m pytest tests/ \
    --ignore=tests/test_daemon.py --ignore=tests/test_driver.py \
    --ignore=tests/test_effects.py --ignore=tests/test_pack.py \
    --ignore=tests/test_soak.py
```
