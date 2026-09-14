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
| 4 | Effects & scheduling | Not started | — needs `oled_hud/clock.py` (`FrameClock`), `oled_hud/effects.py` |

## Acceptance criteria (from handoff)

- [x] `bench.py` numbers recorded in `BENCH.md` (full-frame at 100kHz/1MHz, partial blit at 1MHz)
- [x] Ticker runs 60+ char string (`oled_hud/demos/tape_scroll.py`, confirmed live on hardware) — `FrameClock` itself is Phase 4, so "slack > 0" isn't measurable yet; pacing is still `time.sleep()`
- [x] Zero Pillow/font calls in frame path — `Tape` rasterizes once at construction, `frame()` only slices pre-packed numpy bytes (structural check; `py-spy` verification is Phase 4)
- [x] Partial blit measurably faster than full `show()` for a 2-page region (recorded in `BENCH.md`)
- [ ] Ticker runs 30+ min without drift/leak/I2C errors

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

## Next up

Phase 4 (`oled_hud/clock.py` `FrameClock` + `oled_hud/effects.py`) is the
next gating piece — it can now build on `Tape` instead of raw PIL calls.
