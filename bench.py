"""Phase 0 benchmark harness. Pushes full frames (and, once available, partial
blits) to the SSD1305 and records timing so FPS/scroll-step decisions in later
phases come from measurement, not defaults. Results are appended to BENCH.md.

Run this once at the default I2C clock, then again after bumping
dtparam=i2c_arm_baudrate=1000000 in /boot/firmware/config.txt and rebooting.
After Phase 3 adds oled_hud.driver.PartialSSD1305, this script also times a
256-byte partial (2-page) blit at both clock speeds.
"""

import re
import statistics
import time

import board
import busio
import digitalio
import numpy as np

WIDTH = 128
HEIGHT = 32
N_FRAMES = 300

try:
    from oled_hud.driver import PartialSSD1305 as _Display

    HAVE_BLIT = True
except ImportError:
    from adafruit_ssd1305 import SSD1305_I2C as _Display

    HAVE_BLIT = False


def _make_display():
    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    return _Display(WIDTH, HEIGHT, i2c, reset=reset_pin)


def _stats(samples_s):
    ms = [s * 1000 for s in samples_s]
    ms_sorted = sorted(ms)
    p95_idx = int(len(ms_sorted) * 0.95)
    return statistics.mean(ms), ms_sorted[min(p95_idx, len(ms_sorted) - 1)]


def bench_full_frame(display):
    rng = np.random.default_rng()
    frames = [
        rng.integers(0, 256, size=(HEIGHT // 8) * WIDTH, dtype=np.uint8).tobytes()
        for _ in range(N_FRAMES)
    ]
    samples = []
    for data in frames:
        display.buffer[1:] = data
        t0 = time.perf_counter()
        display.show()
        samples.append(time.perf_counter() - t0)
    return _stats(samples)


def bench_partial_blit(display):
    rng = np.random.default_rng()
    ncols = WIDTH
    page0, page1 = 0, 1  # 2 pages = 256 bytes for full-width, 2-page region
    nbytes = ncols * (page1 - page0 + 1)
    frames = [
        rng.integers(0, 256, size=nbytes, dtype=np.uint8).tobytes()
        for _ in range(N_FRAMES)
    ]
    samples = []
    for data in frames:
        t0 = time.perf_counter()
        display.blit(data, col=0, ncols=ncols, page0=page0, page1=page1)
        samples.append(time.perf_counter() - t0)
    return _stats(samples)


_RUN_RE = re.compile(
    r"(?:- run \d+ — mean: ([\d.]+) ms, p95: ([\d.]+) ms)"
    r"|(?:- mean: ([\d.]+) ms\n- p95: ([\d.]+) ms)"
)


def _merge_result(content: str, heading: str, mean: float, p95: float, floor: float) -> str:
    """Merge one (mean, p95) run into `heading`'s section, appending a new
    section if it doesn't exist yet. A section with a single run keeps the
    plain mean/p95 lines; a second run switches it to numbered "run N" lines
    so multiple runs of the same case stay in one place instead of BENCH.md
    growing a duplicate heading per run."""
    pattern = re.compile(rf"^## {re.escape(heading)}\n(.*?)(?=\n## |\Z)", re.M | re.S)
    match = pattern.search(content)

    pairs = []
    if match is not None:
        for m in _RUN_RE.finditer(match.group(1)):
            if m.group(1) is not None:
                pairs.append((float(m.group(1)), float(m.group(2))))
            else:
                pairs.append((float(m.group(3)), float(m.group(4))))
    pairs.append((mean, p95))

    if len(pairs) == 1:
        body_lines = [f"- mean: {pairs[0][0]:.3f} ms", f"- p95: {pairs[0][1]:.3f} ms"]
    else:
        body_lines = [
            f"- run {i + 1} — mean: {m:.3f} ms, p95: {p:.3f} ms" for i, (m, p) in enumerate(pairs)
        ]
    body_lines.append(f"- I2C-bound floor (bytes*9/baudrate): {floor:.3f} ms")
    new_section = f"## {heading}\n" + "\n".join(body_lines) + "\n"

    if match is None:
        return content.rstrip("\n") + "\n\n" + new_section
    return content[: match.start()] + new_section + content[match.end() :]


def _i2c_baudrate():
    try:
        with open("/boot/firmware/config.txt") as f:
            for line in f:
                if line.strip().startswith("dtparam=i2c_arm_baudrate="):
                    return int(line.strip().split("=")[-1])
    except OSError:
        pass
    return 100_000  # Pi default when unset


def main():
    baud = _i2c_baudrate()
    display = _make_display()

    mean_full, p95_full = bench_full_frame(display)
    print(f"[full frame, 512B] mean={mean_full:.3f}ms p95={p95_full:.3f}ms")
    expected_full = 512 * 9 / baud * 1000
    print(f"  expected I2C-bound floor at {baud}Hz: {expected_full:.3f}ms")

    try:
        with open("BENCH.md") as f:
            content = f.read()
    except FileNotFoundError:
        content = (
            "# I2C / OLED timing benchmarks\n\n"
            "Phase 0 of the animation-engine plan. Numbers below are appended automatically\n"
            "by `bench.py`; FPS and scroll-step choices in later phases come from these,\n"
            "not from defaults. 300 samples per case, `N_FRAMES=300` in `bench.py`.\n"
        )

    content = _merge_result(content, f"{baud}Hz — full frame (512B)", mean_full, p95_full, expected_full)

    if HAVE_BLIT:
        mean_p, p95_p = bench_partial_blit(display)
        print(f"[partial blit, 256B] mean={mean_p:.3f}ms p95={p95_p:.3f}ms")
        expected_p = 256 * 9 / baud * 1000
        content = _merge_result(
            content, f"{baud}Hz — partial blit, 2 pages (256B)", mean_p, p95_p, expected_p
        )
    else:
        print("[partial blit] skipped — oled_hud.driver.PartialSSD1305 not built yet (Phase 3)")

    with open("BENCH.md", "w") as f:
        f.write(content.rstrip("\n") + "\n\n")
    print("Merged results into BENCH.md")

    display.fill(0)
    display.show()


if __name__ == "__main__":
    main()
