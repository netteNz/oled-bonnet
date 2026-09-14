# Animation engine — phase & session tracker

Tracks progress against the original handoff (animation engine for the
2.23" SSD1305 OLED, 128x32). See `README.md` for the repo layout and
`BENCH.md`/`NOTES.md` for the underlying data.

## Phase status

| Phase | What | Status | Artifacts |
|---|---|---|---|
| 0 | Benchmark first | Done | `bench.py`, `BENCH.md` |
| 1 | Driver probe | Partial | `NOTES.md` (addressing, column offset, `write_cmd`). `hw_scroll_spike.py` not written. |
| 2 | Packed-tape core | Done | `oled_hud/pack.py` (`pack_bits`/`pack_image`), `oled_hud/tape.py` (`Tape`), `tests/test_pack.py`, `oled_hud/demos/tape_scroll.py` |
| 3 | Partial-window blitter | Done | `oled_hud/driver.py` (`PartialSSD1305.blit()`) |
| 4 | Effects & scheduling | Done | `oled_hud/clock.py` (`FrameClock`), `oled_hud/effects.py` (`contrast`/`invert`/`all_on`, `Fade`/`Blink`/`Flash`, `EffectQueue`), `tests/test_clock.py`, `tests/test_effects.py`, `oled_hud/demos/ticker.py` |

## Acceptance criteria (from handoff)

- [x] `bench.py` numbers recorded in `BENCH.md` (full-frame at 100kHz/1MHz, partial blit at 1MHz)
- [x] Ticker runs 60+ char string with slack > 0 — `oled_hud/demos/ticker.py` at 30fps: 360 frames in 12.0s, **slack mean 29.9ms / min 27.6ms of a 33.3ms budget**, 0 late, 0 dropped
- [x] Zero Pillow/font calls in frame path — confirmed by `py-spy record` (41 on-CPU samples over a live run): 100% land in `blit()`/`effects.update()`/`clock.tick()` or their I2C callees, 0 in `PIL`/`Image`/`ImageDraw`/`ImageFont`/`render_tape` (not just the structural argument anymore)
- [x] Partial blit measurably faster than full `show()` for a 2-page region (recorded in `BENCH.md`)
- [x] Ticker runs 30+ min without drift/leak/I2C errors — **108,000 frames over 1800.0s at 60fps, 0 late, 0 dropped, 0 errors**; RSS a converged warm-up staircase (35604 -> 35644 KB, tail slope 40 KB/h, nowhere near the 1 MB/h limit); blit p95 flat start-to-end (4.93 -> 4.93ms, -0.1%); `scripts/analyze_soak.py` verdict: **PASS**

## Session log

### 2026-09 — session 1
- Wrote `bench.py`, ran it at 100kHz and 1MHz (Phase 0).
- Wrote `oled_hud/driver.py` (`PartialSSD1305.blit()`) (Phase 3).
- Commit: `a2145c2` "Add I2C benchmark harness and partial-blit OLED driver".

### 2026-09-13 — session 2
- Read `adafruit_ssd1305` source to confirm addressing mode, column offset
  (4, not 0 — SSD1305 ≠ SSD1306), and `write_cmd` visibility; wrote it up
  in `NOTES.md` (Phase 1, partial — no `hw_scroll_spike.py` yet).
- Built `walk.py`: a stick-figure sprite that walks/bounces across the
  display using `blit()`, packing frames with a hand-rolled, pure-Python
  MVLSB packer. This is a prototype outside the formal phase structure —
  it validates `blit()` ergonomics on real hardware but is **not** Phase 2:
  it doesn't use `numpy.packbits`, isn't in the `oled_hud` package, and
  uses `time.sleep()` per frame rather than `FrameClock`. Confirmed working
  live on hardware (clean start/stop, screen clears on exit).
- Created this file and `NOTES.md`.

### 2026-09-13 — session 3
- Implemented Phase 2 per the `oled-animation-handoff.md` addendum
  (corrections to the original sketch: `pack_bits` takes an array not an
  image, and a tape spans only the pages it occupies).
- `oled_hud/pack.py`: `pack_bits()` (numpy, `bitorder="little"`) and
  `pack_image()`.
- `oled_hud/tape.py`: `Tape` — rasterizes once at construction, duplicates
  the leading viewport columns onto the end of the packed loop so `frame()`
  is a single contiguous slice; raises on construction if the tape (text +
  gap) is shorter than the 128px viewport.
- `tests/reference.py`: lifted `image_to_pages()` out of `walk.py`
  unchanged as a golden reference (validated on real hardware).
- `tests/test_pack.py`: 30 cases — golden-reference byte-for-byte match on
  the `walk.py` sprite and several `ImageDraw` text lines, valid heights
  (8/16/32), `ValueError` on a non-multiple-of-8 height, 20 random-shape
  property cases, the 256-byte/2-page frame-size assertion, and the
  short-tape `ValueError`. All green (`pytest` added to `.env`, wasn't
  previously installed).
- `oled_hud/demos/tape_scroll.py`: scrolls a 60+ char string on pages 2-3
  via `Tape.frame()` -> `blit()`; confirmed live on hardware (clean scroll,
  clean Ctrl+C exit, screen cleared, exit code 0).

### 2026-09-13 — session 4
- Implemented Phase 4 (effects & scheduling).
- `oled_hud/clock.py`: `FrameClock` — fixed-timestep pacer. Deadlines are
  `t0 + n * period` from a fixed origin rather than a running sum, so a
  slow frame can't shift every later one (the "no drift" criterion). On an
  overrun it skips the frames it can no longer hit instead of bursting
  through the backlog, and reports per-frame slack plus `stats()`/
  `summary()`. `now`/`sleep` are injectable so tests drive it
  deterministically instead of sleeping.
- `oled_hud/effects.py`: command-register effects (`contrast`, `invert`,
  `all_on`) built on the `write_cmd` escape hatch documented in `NOTES.md`,
  plus non-blocking `Fade`/`Blink`/`Flash` and an `EffectQueue`. Effects are
  driven by elapsed *time*, not frame count, so they run at the same
  wall-clock speed whatever the fps or drop rate; each is self-terminating
  (the update that returns False also restores the resting state).
- `tests/test_clock.py` + `tests/test_effects.py`: 36 new cases (66 total,
  all green) — drift-free deadlines over 1000 jittery frames, overrun and
  drop/resync accounting, slack stats, contrast clamping, the 0xA4/0xA5
  commands, and time-driven (not frame-driven) effect behaviour.
- `oled_hud/demos/ticker.py`: `Tape` + `FrameClock` + `EffectQueue`, with
  `--fps`/`--seconds`/`--step`/`--text`. Confirmed live on hardware:
  30fps run was exactly on target with 0 late / 0 dropped; a deliberate
  400fps overdrive (2.5ms budget vs ~3ms blit) degraded gracefully to
  325fps with 1955 late / 446 dropped — frames + dropped matched the
  expected slot count exactly, so the resync path is validated on the panel
  and not just against the fake clock.

### 2026-09-13 — session 5 (soak instrumentation + run)
- Added `oled_hud/soak.py` (`SoakRecorder`, `BlitTimer`), `--soak-log`/
  `--bucket-s` + a `SIGTERM` handler on `ticker.py`, and
  `scripts/analyze_soak.py`. 98 tests green (28 new). Verified end-to-end
  on hardware before the real run: a 17s rehearsal produced correct
  per-bucket JSONL and passed the analyzer; a `SIGTERM` mid-run (via a
  script file, not an inline heredoc — an inline `$!`/`kill` sequence
  silently lost its newlines and never signaled the right PID) exited 0
  with the final partial bucket flushed.
- Pre-soak check turned up two orphaned `ticker.py` processes left running
  from the SIGTERM test (broken test harness, not the SIGTERM code — see
  above), racing each other on I2C. Killed them; the race had left two
  stray white lines in GDDRAM at the bottom of the panel, cleared by a
  full hardware reset (toggles the reset pin, replays `init_display()`)
  rather than a plain `fill(0)`. Confirmed by eye after the reset.
- Soak run started at the recorded state below.

#### Pre-run state — 2026-09-13 23:5x
- git SHA: `5b0aa86`
- I2C: `dtparam=i2c_arm_baudrate=1000000` in `/boot/firmware/config.txt`,
  confirmed **live** via `/proc/device-tree/soc/i2c@7e804000/clock-frequency`
  = `1000000` (i2c-1, what `board.SCL`/`board.SDA` resolve to) — not just
  configured, actually running at 1MHz.
- No other Python/oled process running; load average settled to ~0.85
  after killing the orphaned tickers above.
- Config: `--fps 60 --step 1 --seconds 1800`, production config per the
  handoff (not the 400fps overdrive already validated separately).
- No `tmux` on this Pi and no passwordless `sudo` to install it; launched
  via `setsid`+`nohup`+`disown` instead, which satisfies the actual
  requirement (survives a dropped connection) without the interactive
  attach/detach `tmux` gives. It worked: the run completed the full 30
  minutes across a session gap on the order of hours.

#### Result — `runs/soak-20260913-2351.jsonl`, 30 buckets
- **108,000 frames in 1800.0s at exactly 60.0 fps — 0 late, 0 dropped, 0 errors.**
- `scripts/analyze_soak.py --exit-code 0` verdict: **PASS**, all 5 checks green.
- RSS: 35604 -> 35644 KB, a converged staircase (steps at buckets 1, 14,
  16, 25, each held flat for many buckets after) — overall slope +107.5
  KB/h, but the check that actually matters, tail-only slope (last third),
  is +40 KB/h: two orders of magnitude under the 1 MB/h limit and visibly
  flat for the run's last several minutes. Not a leak.
- **Found and fixed a real bug in `analyze_soak.py` from this run**: the
  original "monotonic across the whole run" leak check flagged this
  plateaued staircase as a FAIL, because a converged staircase never ticks
  down and reads identically to an unbounded climb under that definition.
  Replaced it with a second least-squares slope computed over just the
  last third of buckets — a staircase that has plateaued shows a near-zero
  tail slope, an active leak doesn't. Added `test_a_plateaued_staircase_is_not_a_leak`
  and `test_a_climb_that_starts_late_is_still_caught` to lock in both
  directions.
- blit p95: 4.93 -> 4.93ms, essentially flat (-0.1%), well inside the ±15%
  tail-drift budget.
- Slack floor 2.0ms (worst single frame of 108,000) against a 16.7ms
  budget — comfortable even at the tightest moment of a 30-minute run.

#### `py-spy` — frame-path confirmation
No passwordless `sudo`, and plain `py-spy dump`/`record` failed with
`Failed to find python version from target process` under this
environment's default sandboxing (a ptrace restriction, not a py-spy or
code problem) — resolved by running the two-process py-spy attach with
sandboxing disabled for that one command (`dangerouslyDisableSandbox`;
attaching to our own already-running, already-open-source demo process).
`py-spy record -f raw -d 9 -r 200 --nonblocking` over a live 30fps run
collected 41 on-CPU samples (nonblocking + a mostly-idle-in-sleep loop
means most of each frame's slack is invisible to the sampler by design —
expected, not an error). All 41 land at one of the frame loop's three
lines (`ticker.py:100` `blit()`, `:101` `effects.update()`, `:105`
`clock.tick()`) or their direct I2C callees; zero contain `PIL`, `Image`,
`ImageDraw`, `ImageFont`, or `render_tape`. Closes that criterion with
profiler evidence instead of the structural argument alone.

All five animation-engine phases are implemented and every acceptance
criterion from the original handoff is checked off. The Phase 1
`hw_scroll_spike.py` noted in `NOTES.md` remains open but never blocking,
since the tape approach avoids hardware scroll entirely.

## HUD daemon (oled-hud-handoff.md)

New handoff, new phase namespace (`H0`-`H4`), built on top of the animation
engine above. Order per the handoff: H0 → H1 → H3 → H2 → H4 (lifecycle
before scheduler, since a daemon that can be run twice is the
highest-probability failure).

| Phase | What | Status | Artifacts |
|---|---|---|---|
| H0 | Compositor diff/coalesce engine | Done | `oled_hud/hud/compositor.py` (`Compositor`, `plan_run`, `cost`), `tests/test_compositor.py`, `oled_hud/demos/compositor_static.py` |
| H1 | Store, producers, first real view | Not started | |
| H3 | Lifecycle: singleton, reset, systemd | Code done, systemd unit not installed | `oled_hud/hud/daemon.py`, `tests/test_daemon.py`, `systemd/oled-hud.service` |
| H2 | Views, scheduler, preemption, transitions | Not started | |
| H4 | Buttons and burn-in | Not started | |

### 2026-09-14 — session 6 (Phase H0)
- `oled_hud/hud/compositor.py`: `Compositor` owns `fb`/`pushed` (4,128)
  uint8 page-major arrays; widgets write into `fb` by slicing, `flush()`
  diffs against `pushed` and pushes the minimum. `plan_run()` picks between
  one push spanning a dirty-page run's column union and one push per page
  narrowed to its own dirty columns, by the `BENCH.md` cost model
  (`FIXED_MS = 0.6`, `PER_BYTE_MS = 0.009`, both kept as named constants
  pointing back at the measurement, not tuning knobs). `force_full()` is a
  one-shot flag consumed by the next `flush()`, for use after a hardware
  reset or a burn-in offset change (H4) invalidates the `pushed` cache.
- `tests/test_compositor.py`: 28 cases against a fake driver (records
  `blit()` calls, no hardware) — every case from the handoff's acceptance
  list (no-op flush, single-byte push, non-adjacent pages never coalesced,
  the two-full-pages ticker case, the two-hotspot case where the per-page
  plan must beat the union, `force_full()`'s one 512-byte push and its
  one-shot behavior on the following flush), plus a `pushed == fb`
  round-trip property over 20 random framebuffers and a plan-coverage
  property (no dirty byte is ever left unpushed) over 50 random dirty
  masks. All green; full suite 128/128.
- `oled_hud/demos/compositor_static.py`: renders one static line via PIL
  once, then runs a plain `FrameClock` loop calling only `flush()`,
  printing pushes-per-frame. Confirmed live on hardware: frame 0 pushes
  once (the dirty text region), frames 1-89 push zero — matches the H0
  acceptance criterion exactly, at 29.9fps/3s with 0 late/dropped.

### 2026-09-14 — session 6 (Phase H3, continued)
- `oled_hud/hud/daemon.py`: `acquire_singleton_lock()` (exclusive
  non-blocking `flock` on `~/.oled-hud.lock`, kept open for process
  lifetime so the kernel releases it on exit *or* crash — no pidfile
  staleness possible), `reset_display()` (constructing `PartialSSD1305`
  fresh toggles the reset pin and replays `init_display()` — confirmed by
  reading `poweron()`/`init_display()` in the installed
  `adafruit_ssd1305` source rather than assumed), and `main()`: acquire
  lock before any hardware I/O → reset → `compositor.force_full()` → run.
  SIGTERM sets a stop flag the same way `ticker.py` does; shutdown clears
  the panel and closes the lock file. Since H1/H2 don't exist yet,
  `render_placeholder()` draws a name+uptime view through the real
  `Compositor` so the lifecycle is visually checkable today — it's
  explicitly throwaway, replaced once `views.py`/`scheduler.py` land.
- `tests/test_daemon.py`: 3 cases against `tmp_path` (no hardware) — a
  second `acquire_singleton_lock()` call on a held path raises
  `SystemExit`, closing the first releases it for a second caller, and a
  direct `flock` probe confirms the lock is actually held, not just the
  file opened. Full suite 131/131.
- `systemd/oled-hud.service`: unit template (`ExecStart` pointed at this
  repo's `.env`, `Restart=on-failure`, `RestartSec=2`).
- Verified live, as a plain foreground process (systemd not yet
  installed): a second launch while the first holds the lock exits 1 with
  the lock message; `SIGTERM` (via `timeout`) drains cleanly — frame
  summary printed, panel cleared, lock released — and a fresh launch
  right after succeeds; `kill -9` leaves no stale lock (kernel-released)
  and the next launch's `reset_display()` recovers the panel, same fix as
  session 5's orphaned-ticker incident. `systemctl`-level acceptance
  (`restart` mid-run, `kill -9` → auto-restart by systemd, `loginctl
  enable-linger`) needs the actual unit installed, which needs one-time
  `sudo` and starts a persistent background service holding the I2C bus —
  held for explicit user go-ahead rather than done unprompted.

### 2026-09-14 — session 7 (playground demos, outside the phase structure)
Same spirit as `walk.py` in session 2: exercises `Compositor` directly with
real moving/reactive content, ahead of and separate from H1's
Store/producers/views. Neither demo is a phase deliverable.

- `oled_hud/demos/hud_animate.py`: a bouncing square on pages 0-1 and a
  scrolling random-walk sparkline on pages 2-3, both numpy straight into
  `Compositor.fb` (no PIL). Since both widgets change every frame, this is
  the opposite case from `compositor_static.py` — it exercises `plan_run()`
  choosing between a union push and a per-page plan on genuinely moving
  content, not the settle-to-zero static case. Confirmed live at 60fps:
  480 frames, only 1 late, 3.71 pushes/frame averaging 185 bytes — the
  cost model correctly favors per-page pushes here since the sparkline
  dirties nearly the full width on pages 2-3 while the ball only dirties a
  narrow strip on pages 0-1.
- `oled_hud/demos/audio_visualizer.py`: a live FFT spectrum analyzer off a
  USB mic (a HyperX USB audio device, `sounddevice` device index 1 — no
  built-in capture hardware on this Pi; needed `sudo apt install
  portaudio19-dev` first, done by the user, then `pip install sounddevice`
  into `.env`). `LatestBlock` holds only the newest mic block (written by
  `sounddevice`'s own callback thread under a lock, read by the render
  loop) — no ring buffer needed since a visualizer only ever wants the
  newest window. 32 log-spaced bars (`bar_edges`/`bin_bars`), fast-attack /
  slow-release envelope, drawn via `pack_bits` straight into `fb`.
  Confirmed live at 30fps for a full minute: 1800 frames, 0 late, 0
  dropped, min slack 9.7ms of a 33.3ms budget even with the FFT work per
  frame.
  - **Bug found live, fixed**: the two lowest bars never lit up. Cause: at
    `WINDOW=2048` samples/44100Hz, each FFT bin is ~21.5Hz wide, but the
    lowest log-spaced bars span only ~8-10Hz — no bin ever fell inside
    them, so `bin_bars` left them at zero forever. Fixed by zero-padding
    the FFT to `FFT_SIZE=8192` before `rfft` (interpolates the spectrum to
    ~5.4Hz bins, no added capture latency since the captured window is
    still 2048 samples) plus a nearest-bin fallback in `bin_bars` for any
    band that still ends up narrower than one bin. Verified with a
    synthetic broadband spectrum: 0 of 32 bars zero, versus dead bars
    before the fix.
  - **Tuning requested live**: high-frequency bars read quiet even with
    audible treble present — real audio's natural spectral roll-off, not a
    binning bug. Added `TILT_DB_PER_OCTAVE = 4.5`, a per-bar dB boost
    proportional to `log2(center_freq / FMIN)` (~38dB boost at the top bar
    vs. the bottom one for 32 bars over 40Hz-16kHz), applied after `bin_bars`
    and before the dB-to-height normalization. Not yet confirmed live
    against the fix (user hasn't reported back on the re-run).

## Next up

Two things need the user before more code gets written:
- **Installing `systemd/oled-hud.service`** — `sudo loginctl
  enable-linger`, `systemctl --user enable --now`, and the
  `restart`/`kill -9`-under-systemd acceptance checks from the handoff.
  Everything under the daemon's own control is already verified above.
- **H1** needs the Prometheus / Pi-hole / Alertmanager endpoints (config
  file outside the repo, per the handoff's security posture) and a font
  choice to vendor via `scripts/build_fonts.py` (Spleen and Tom Thumb are
  both handoff-approved). Inputs only the user can supply.

H2 (scheduler) and H4 (buttons/burn-in) follow once those land, per the
handoff's stated order.
