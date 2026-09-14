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
- [x] Zero Pillow/font calls in frame path — `Tape` rasterizes once at construction, `frame()` only slices pre-packed numpy bytes (structural check; `py-spy` still not run)
- [x] Partial blit measurably faster than full `show()` for a 2-page region (recorded in `BENCH.md`)
- [ ] Ticker runs 30+ min without drift/leak/I2C errors — runnable now via `ticker.py --seconds 1800`, but only 12s has actually been observed

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

## Next up

All five phases from the handoff are implemented. Remaining open items:
the 30+ min soak (`ticker.py --seconds 1800`), `py-spy` confirmation of the
frame path, and the Phase 1 `hw_scroll_spike.py` noted in `NOTES.md`.
