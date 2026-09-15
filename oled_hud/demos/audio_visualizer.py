"""Live audio spectrum visualizer through the Compositor (H0) -- no PIL,
just numpy. A `sounddevice` input callback drops the latest block of mic
samples into a small shared buffer (its own audio thread, never blocks the
render loop); the render loop FFTs whatever's latest, bins it into
log-spaced bars, smooths with a fast-attack/slow-release envelope, and
draws straight into `Compositor.fb`.

Needs a capture device -- run with `--list` to see what sounddevice sees,
`--device` to pick one if the default input isn't the mic you want.

Run with:
    .env/bin/python3 -m oled_hud.demos.audio_visualizer --list
    .env/bin/python3 -m oled_hud.demos.audio_visualizer
    .env/bin/python3 -m oled_hud.demos.audio_visualizer --seconds 30 --bars 32
    .env/bin/python3 -m oled_hud.demos.audio_visualizer --device 1
    .env/bin/python3 -m oled_hud.demos.audio_visualizer --style mirror
"""

import argparse
import signal
import sys
import threading

import board
import busio
import digitalio
import numpy as np
import sounddevice as sd

from oled_hud.clock import FrameClock
from oled_hud.driver import PartialSSD1305
from oled_hud.hud.compositor import Compositor
from oled_hud.pack import pack_bits

WIDTH = 128
HEIGHT = 32
HALF = HEIGHT // 2
SAMPLE_RATE = 44100
WINDOW = 2048  # samples captured per frame == the callback's blocksize
FFT_SIZE = 8192  # zero-padded rfft size: interpolates the spectrum to a finer
# bin spacing (5.4Hz vs WINDOW's 21.5Hz) without adding capture latency --
# the audio window is still WINDOW samples, just padded before the FFT. At
# WINDOW's native spacing the lowest log-spaced bars are narrower than one
# bin and always read zero; this is what fixes that instead of widening FMIN.
FMIN, FMAX = 40.0, 16000.0
DB_FLOOR, DB_CEIL = -60.0, 0.0
ATTACK, RELEASE = 0.6, 0.15  # envelope follow rate per frame, tuned by eye
TILT_DB_PER_OCTAVE = 4.5  # boosts bars by frequency to counter real audio's
# natural high-end roll-off (pink-noise-ish spectral tilt) -- without this the
# raw spectrum genuinely is quieter up top and treble bars barely move even
# when there's audible high end. Tuned by eye against a live mic.


class LatestBlock:
    """Holds only the most recent audio block. The callback thread
    overwrites it; the render thread copies it out under the same lock.
    A visualizer doesn't need every sample, only the newest window, so
    there's no ring buffer or queue to manage.
    """

    def __init__(self, size: int):
        self._data = np.zeros(size, dtype=np.float32)
        self._lock = threading.Lock()
        self._ready = False

    def put(self, block: np.ndarray) -> None:
        with self._lock:
            self._data[:] = block
            self._ready = True

    def get(self):
        with self._lock:
            return self._data.copy(), self._ready


def bar_edges(n_bars: int, fmin: float, fmax: float) -> np.ndarray:
    return np.geomspace(fmin, fmax, n_bars + 1)


def bin_bars(spec: np.ndarray, freqs: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Log-spaced frequency bars (peak magnitude per band) -- log spacing
    matches how pitch is actually perceived, so bass and treble both get
    visible bars instead of bass dominating a linear split.
    """
    n_bars = len(edges) - 1
    bars = np.zeros(n_bars)
    for i in range(n_bars):
        mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if mask.any():
            bars[i] = spec[mask].max()
        else:
            # A band narrower than the bin spacing has no bin inside its
            # edges -- fall back to the single nearest bin so the bar
            # tracks the signal instead of reading zero forever.
            center = np.sqrt(edges[i] * edges[i + 1])
            bars[i] = spec[np.argmin(np.abs(freqs - center))]
    return bars


def draw_bars(fb: np.ndarray, env: np.ndarray, gap: int = 1) -> None:
    n_bars = len(env)
    bar_w = WIDTH // n_bars
    bits = np.zeros((HEIGHT, WIDTH), dtype=bool)
    for i, h in enumerate(env):
        h = int(np.clip(h, 0, HEIGHT))
        if h == 0:
            continue
        c0 = i * bar_w
        c1 = c0 + max(bar_w - gap, 1)
        bits[HEIGHT - h :, c0:c1] = True
    fb[...] = pack_bits(bits)


def draw_bars_mirror(fb: np.ndarray, env: np.ndarray, gap: int = 1) -> None:
    """Same bars, grown from the vertical center outward in both
    directions instead of from the bottom -- `env` is expected scaled to
    HALF (HEIGHT // 2), not HEIGHT, so a maxed-out bar spans the full panel.
    """
    n_bars = len(env)
    bar_w = WIDTH // n_bars
    bits = np.zeros((HEIGHT, WIDTH), dtype=bool)
    for i, h in enumerate(env):
        h = int(np.clip(h, 0, HALF))
        if h == 0:
            continue
        c0 = i * bar_w
        c1 = c0 + max(bar_w - gap, 1)
        bits[HALF - h : HALF + h, c0:c1] = True
    fb[...] = pack_bits(bits)


def open_stream(device, samplerate: int, blocksize: int, latest: LatestBlock) -> sd.InputStream:
    def callback(indata, frames, time_info, status):
        mono = indata[:, 0] if indata.ndim > 1 and indata.shape[1] > 1 else indata.reshape(-1)
        latest.put(mono.astype(np.float32, copy=False))

    try:
        return sd.InputStream(
            device=device, channels=1, samplerate=samplerate, blocksize=blocksize, callback=callback
        )
    except sd.PortAudioError:
        # Some capture devices refuse to open mono; take channel 0 of stereo instead.
        return sd.InputStream(
            device=device, channels=2, samplerate=samplerate, blocksize=blocksize, callback=callback
        )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--seconds", type=float, default=0.0, help="0 runs until Ctrl+C")
    parser.add_argument("--bars", type=int, default=32)
    parser.add_argument(
        "--style", choices=["bars", "mirror"], default="bars",
        help="bars grow from the bottom, mirror grows from the vertical center outward",
    )
    parser.add_argument("--device", default=None, help="sounddevice index or name substring")
    parser.add_argument("--list", action="store_true", help="list audio devices and exit")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.list:
        print(sd.query_devices())
        return

    if WIDTH % args.bars:
        sys.exit(f"--bars must divide {WIDTH} evenly, got {args.bars}")

    device = args.device
    if device is not None:
        try:
            device = int(device)  # sounddevice treats a bare numeric string as a name query, not an index
        except ValueError:
            pass

    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    display = PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)
    display.fill(0)
    display.show()

    comp = Compositor(display)
    latest = LatestBlock(WINDOW)
    window_fn = np.hanning(WINDOW)
    padded = np.zeros(FFT_SIZE, dtype=np.float32)
    freqs = np.fft.rfftfreq(FFT_SIZE, 1.0 / SAMPLE_RATE)
    env = np.zeros(args.bars)
    draw = draw_bars_mirror if args.style == "mirror" else draw_bars
    scale = HALF if args.style == "mirror" else HEIGHT

    edges = bar_edges(args.bars, FMIN, FMAX)
    centers = np.sqrt(edges[:-1] * edges[1:])
    tilt_db = TILT_DB_PER_OCTAVE * np.log2(centers / FMIN)

    # SIGTERM (e.g. a background-task stop) takes the same clean-exit path
    # as Ctrl+C, so the panel gets cleared either way instead of being left
    # showing a stale frame.
    stopping = []
    signal.signal(signal.SIGTERM, lambda *_: stopping.append(True))

    clock = FrameClock(args.fps)
    with open_stream(device, SAMPLE_RATE, WINDOW, latest):
        try:
            while not stopping and (not args.seconds or clock.elapsed < args.seconds):
                samples, ready = latest.get()
                if ready:
                    padded[:WINDOW] = samples * window_fn
                    spec = np.abs(np.fft.rfft(padded))
                    bars = bin_bars(spec, freqs, edges)
                    db = 20 * np.log10(bars + 1e-6) + tilt_db
                    target = np.clip((db - DB_FLOOR) / (DB_CEIL - DB_FLOOR), 0.0, 1.0) * scale
                    rate = np.where(target > env, ATTACK, RELEASE)
                    env += (target - env) * rate

                draw(comp.fb, env)
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
