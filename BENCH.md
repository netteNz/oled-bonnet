# I2C / OLED timing benchmarks

Phase 0 of the animation-engine plan. Numbers below are appended automatically
by `bench.py`; FPS and scroll-step choices in later phases come from these,
not from defaults. 300 samples per case, `N_FRAMES=300` in `bench.py`.
## 100000Hz — full frame (512B)
- mean: 52.168 ms
- p95: 72.470 ms
- I2C-bound floor (bytes*9/baudrate): 46.080 ms

## 1000000Hz — full frame (512B)
- run 1 — mean: 5.217 ms, p95: 5.504 ms
- run 2 — mean: 5.178 ms, p95: 5.375 ms
- run 3 — mean: 5.171 ms, p95: 5.338 ms
- I2C-bound floor (bytes*9/baudrate): 4.608 ms

## 1000000Hz — partial blit, 2 pages (256B)
- run 1 — mean: 3.216 ms, p95: 4.396 ms
- run 2 — mean: 2.903 ms, p95: 3.198 ms
- I2C-bound floor (bytes*9/baudrate): 2.304 ms

