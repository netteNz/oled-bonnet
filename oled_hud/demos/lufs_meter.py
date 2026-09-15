"""Live LUFS loudness meter through the Compositor (H0) -- no PIL, just
numpy. Same mic capture plumbing as audio_visualizer.py (reused from
there), but instead of an FFT spectrum this runs the audio through the
ITU-R BS.1770-4 K-weighting filter and displays momentary loudness
(400ms window) as a scrolling history graph -- "rolling LUFS" rather than
raw amplitude, so it reflects perceived loudness rather than peak level.

K-weighting is two cascaded biquads (a high-shelf "head effects" stage,
then a high-pass "RLB" stage). The standard publishes fixed coefficients
for 48kHz; ours are derived for SAMPLE_RATE via the filters' analog design
parameters (f0/Q/gain) and the bilinear transform, the same rate-agnostic
approach pyloudnorm uses -- verified by re-deriving at 48kHz and checking
the result against the standard's published 48kHz coefficients (matches
to 1e-9).

Only momentary (400ms) and short-term (3s) loudness are computed -- not
"integrated" programme loudness, which additionally needs BS.1770's
two-stage absolute/relative gating algorithm and is a summary number for
a whole session, not something a live per-frame meter needs.

Needs a capture device -- run with `--list` to see what sounddevice sees.

Run with:
    .env/bin/python3 -m oled_hud.demos.lufs_meter --list
    .env/bin/python3 -m oled_hud.demos.lufs_meter --device 1
    .env/bin/python3 -m oled_hud.demos.lufs_meter --device 1 --seconds 30
    .env/bin/python3 -m oled_hud.demos.lufs_meter --device 1 --floor -90 --ceil -50
"""

import argparse
import signal
import threading

import board
import busio
import digitalio
import numpy as np
import sounddevice as sd

from oled_hud.clock import FrameClock
from oled_hud.demos.audio_visualizer import SAMPLE_RATE, WINDOW, open_stream
from oled_hud.driver import PartialSSD1305
from oled_hud.hud.compositor import Compositor
from oled_hud.pack import pack_bits

WIDTH = 128
HEIGHT = 32

MOMENTARY_S = 0.4  # BS.1770 "M" window
SHORT_TERM_S = 3.0  # BS.1770 "S" window
LUFS_FLOOR, LUFS_CEIL = -60.0, 0.0  # default display range mapped to panel height;
# override with --floor/--ceil for a weak input chain (e.g. a headphone-out
# tapped into a mic-in reads far quieter than a proper line-level signal)
# One column shifts in per *captured* block (WINDOW / SAMPLE_RATE seconds
# apart), so the visible history spans WIDTH * WINDOW / SAMPLE_RATE seconds
# -- about 6s at the defaults -- as long as --fps is at least the block
# rate (SAMPLE_RATE / WINDOW, ~21.5Hz here) so no block goes unconsumed.


class NewestBlock:
    """Like audio_visualizer's LatestBlock, but `get_new()` clears the
    ready flag on read -- a LUFS window must consume each captured block
    exactly once (reprocessing a block would double-count its energy in
    the sliding window; audio_visualizer's FFT view doesn't care about
    that since redrawing the same spectrum twice is harmless).
    """

    def __init__(self, size: int):
        self._data = np.zeros(size, dtype=np.float32)
        self._lock = threading.Lock()
        self._new = False

    def put(self, block: np.ndarray) -> None:
        with self._lock:
            self._data[:] = block
            self._new = True

    def get_new(self):
        with self._lock:
            if not self._new:
                return None
            self._new = False
            return self._data.copy()


class Biquad:
    """Direct-form-II-transposed biquad with state carried across blocks,
    since K-weighting is a continuous filter over the whole stream, not
    something that can be reset every block.
    """

    def __init__(self, b0: float, b1: float, b2: float, a1: float, a2: float):
        self.b0, self.b1, self.b2, self.a1, self.a2 = b0, b1, b2, a1, a2
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        b0, b1, b2, a1, a2 = self.b0, self.b1, self.b2, self.a1, self.a2
        x1, x2, y1, y2 = self.x1, self.x2, self.y1, self.y2
        y = np.empty_like(x)
        for i, xi in enumerate(x):
            yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            y[i] = yi
            x2, x1 = x1, xi
            y2, y1 = y1, yi
        self.x1, self.x2, self.y1, self.y2 = x1, x2, y1, y2
        return y


def high_shelf_biquad(rate: float) -> Biquad:
    """BS.1770's "pre-filter" stage (head-effects high shelf). Constants
    are the filter's analog design parameters (center freq/gain/Q), not
    the 48kHz-specific coefficients -- rederiving them per SAMPLE_RATE via
    the bilinear transform is what makes this correct at 44.1kHz.
    """
    f0, gain_db, q = 1681.9744509555319, 3.99984385397, 0.7071752369554193
    k = np.tan(np.pi * f0 / rate)
    vh = 10 ** (gain_db / 20)
    vb = vh**0.4996667741545416
    a0 = 1.0 + k / q + k * k
    b0 = (vh + vb * k / q + k * k) / a0
    b1 = 2.0 * (k * k - vh) / a0
    b2 = (vh - vb * k / q + k * k) / a0
    a1 = 2.0 * (k * k - 1.0) / a0
    a2 = (1.0 - k / q + k * k) / a0
    return Biquad(b0, b1, b2, a1, a2)


def high_pass_biquad(rate: float) -> Biquad:
    """BS.1770's RLB-weighting stage (high-pass, removes rumble)."""
    f0, q = 38.13547087613982, 0.5003270373238773
    k = np.tan(np.pi * f0 / rate)
    a0 = 1.0 + k / q + k * k
    a1 = 2.0 * (k * k - 1.0) / a0
    a2 = (1.0 - k / q + k * k) / a0
    return Biquad(1.0, -2.0, 1.0, a1, a2)


class RollingWindow:
    """Fixed-size trailing window of K-weighted samples, updated one
    captured block at a time -- a plain shift-and-append since sizes here
    (tens of thousands of samples) are cheap for numpy to memmove at the
    ~20 blocks/sec this gets pushed.
    """

    def __init__(self, size: int):
        self.buf = np.zeros(size, dtype=np.float64)

    def push(self, block: np.ndarray) -> None:
        n = len(block)
        if n >= len(self.buf):
            self.buf[:] = block[-len(self.buf) :]
        else:
            self.buf[:-n] = self.buf[n:]
            self.buf[-n:] = block

    def lufs(self) -> float:
        mean_sq = float(np.mean(self.buf**2))
        return -0.691 + 10 * np.log10(mean_sq + 1e-12)


def draw_scroll(fb: np.ndarray, history: np.ndarray, floor: float, ceil: float) -> None:
    heights = np.clip((history - floor) / (ceil - floor), 0.0, 1.0) * HEIGHT
    rows = np.arange(HEIGHT)[:, None]
    bits = rows >= (HEIGHT - heights.astype(np.int64))[None, :]
    fb[...] = pack_bits(bits)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fps", type=float, default=25.0, help="should be >= the ~21.5Hz audio block rate")
    parser.add_argument("--seconds", type=float, default=0.0, help="0 runs until Ctrl+C")
    parser.add_argument("--device", default=None, help="sounddevice index or name substring")
    parser.add_argument("--list", action="store_true", help="list audio devices and exit")
    parser.add_argument("--print-interval", type=float, default=1.0, help="seconds between M/S LUFS prints")
    parser.add_argument("--floor", type=float, default=LUFS_FLOOR, help="LUFS mapped to an empty graph")
    parser.add_argument("--ceil", type=float, default=LUFS_CEIL, help="LUFS mapped to a full-height graph")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.list:
        print(sd.query_devices())
        return

    device = args.device
    if device is not None:
        try:
            device = int(device)
        except ValueError:
            pass

    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    display = PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)
    display.fill(0)
    display.show()

    comp = Compositor(display)
    latest = NewestBlock(WINDOW)

    shelf = high_shelf_biquad(SAMPLE_RATE)
    hpass = high_pass_biquad(SAMPLE_RATE)
    momentary = RollingWindow(int(MOMENTARY_S * SAMPLE_RATE))
    short_term = RollingWindow(int(SHORT_TERM_S * SAMPLE_RATE))

    scroll = np.full(WIDTH, args.floor)

    # SIGTERM (e.g. a background-task stop) takes the same clean-exit path
    # as Ctrl+C, so the panel gets cleared either way instead of being left
    # showing a stale frame.
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))

    clock = FrameClock(args.fps)
    last_print = 0.0
    with open_stream(device, SAMPLE_RATE, WINDOW, latest):
        try:
            while not stopping and (not args.seconds or clock.elapsed < args.seconds):
                block = latest.get_new()
                if block is not None:
                    filtered = hpass.process(shelf.process(block.astype(np.float64)))
                    momentary.push(filtered)
                    short_term.push(filtered)
                    m_lufs = momentary.lufs()
                    scroll[:-1] = scroll[1:]
                    scroll[-1] = m_lufs

                    if clock.elapsed - last_print >= args.print_interval:
                        print(f"M={m_lufs:6.1f} LUFS  S={short_term.lufs():6.1f} LUFS")
                        last_print = clock.elapsed

                draw_scroll(comp.fb, scroll, args.floor, args.ceil)
                comp.flush()
                clock.tick()
        except KeyboardInterrupt:
            pass
        finally:
            display.fill(0)
            display.show()
            print(clock.summary())


if __name__ == "__main__":
    main()
