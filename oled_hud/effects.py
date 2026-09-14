"""Display effects (Phase 4).

Everything here drives the SSD1305's command registers rather than the
framebuffer. Contrast, inverse video and entire-display-on are two-byte
I2C writes each, so an effect costs a few tens of microseconds instead of
a 256-byte blit — it rides along in the frame budget without competing
with the ticker for bandwidth. `NOTES.md` (Phase 1) confirms `write_cmd`
is public on `SSD1305_I2C`, which is the escape hatch `all_on()` needs.

The `Effect` classes are non-blocking and driven by elapsed *time*, not
frame count, so they run at the same wall-clock speed whatever fps the
`FrameClock` is holding and however many frames it drops. Each one is
self-terminating: the update that returns False also restores the display
to its resting state, so a caller that just drops the effect is safe.
"""

from collections import deque

from adafruit_ssd1305 import SET_ENTIRE_ON

CONTRAST_MAX = 0xFF


def contrast(display, value: int) -> int:
    """Set contrast, clamped to 0..255. Returns the value actually sent."""
    clamped = max(0, min(CONTRAST_MAX, int(value)))
    display.contrast(clamped)
    return clamped


def invert(display, on: bool) -> None:
    """Swap lit and unlit pixels (0xA6/0xA7) without touching GDDRAM."""
    display.invert(bool(on))


def all_on(display, on: bool) -> None:
    """Light every pixel regardless of GDDRAM (0xA5), or go back to it (0xA4)."""
    display.write_cmd(SET_ENTIRE_ON | bool(on))


class Effect:
    """Base class. `update()` is called every frame with seconds-since-start
    and returns True while the effect is still running."""

    duration = 0.0

    def update(self, display, t: float) -> bool:
        raise NotImplementedError


class Fade(Effect):
    """Linear contrast ramp from `start` to `end` over `duration` seconds."""

    def __init__(self, start: int, end: int, duration: float):
        if duration <= 0:
            raise ValueError(f"duration must be positive, got {duration}")
        self.start = start
        self.end = end
        self.duration = duration
        self._last = None

    def update(self, display, t: float) -> bool:
        progress = min(max(t / self.duration, 0.0), 1.0)
        value = round(self.start + (self.end - self.start) * progress)
        if value != self._last:
            # Skip redundant writes — a slow ramp would otherwise resend the
            # same contrast byte for many frames in a row.
            self._last = contrast(display, value)
        return t < self.duration


class Blink(Effect):
    """Flash inverse video `times` times, each flash `period` seconds long
    (first half inverted, second half normal)."""

    def __init__(self, period: float = 0.25, times: int = 3):
        if period <= 0:
            raise ValueError(f"period must be positive, got {period}")
        self.period = period
        self.times = times
        self.duration = period * times
        self._state = None

    def update(self, display, t: float) -> bool:
        running = t < self.duration
        on = running and (t % self.period) < self.period / 2
        if on != self._state:
            self._state = on
            invert(display, on)
        return running


class Flash(Effect):
    """Light the whole panel for `duration` seconds, then return to GDDRAM."""

    def __init__(self, duration: float = 0.08):
        if duration <= 0:
            raise ValueError(f"duration must be positive, got {duration}")
        self.duration = duration
        self._state = None

    def update(self, display, t: float) -> bool:
        running = t < self.duration
        if running != self._state:
            self._state = running
            all_on(display, running)
        return running


class EffectQueue:
    """Runs queued effects one after another off a monotonic time source.

    Non-blocking: call `update()` once per frame with the current time and
    it advances whatever is playing. When one effect ends, the next starts
    in the same frame rather than idling until the next one.
    """

    def __init__(self):
        self._queue = deque()
        self._t0 = None

    def play(self, effect: Effect) -> None:
        self._queue.append(effect)

    @property
    def busy(self) -> bool:
        return bool(self._queue)

    def update(self, display, now: float) -> bool:
        """Advance the queue. Returns True if something is still playing."""
        while self._queue:
            effect = self._queue[0]
            if self._t0 is None:
                self._t0 = now
            if effect.update(display, now - self._t0):
                return True
            self._queue.popleft()
            self._t0 = None
        return False
