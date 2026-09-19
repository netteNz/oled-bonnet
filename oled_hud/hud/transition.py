"""Horizontal slide transition between two views (Phase H2).

Framebuffers here are the same `(PAGES, WIDTH)` uint8 arrays as
`Compositor.fb` -- page-major MVLSB, one byte per column holding 8 vertical
pixels (see `oled_hud.pack`). That layout is exactly why a *horizontal*
slide is nearly free and a *vertical* one would not be: sliding sideways is
pure column indexing, `np.concatenate` of two slices with no unpacking, no
bit shifting and no PIL. A vertical slide would need to shift bits within
every byte and carry across page boundaries -- a much worse deal on this
panel. Don't generalize this module to a vertical slide without redoing that
math from scratch.

Like `oled_hud.effects.Effect`, a `Slide` is driven by elapsed *time* since it
started, not by frame count, so a transition takes the same wall-clock time
at 60fps or 15fps and however many frames the `FrameClock` drops.
"""

import numpy as np

from oled_hud.hud import WIDTH


def slide(old: np.ndarray, new: np.ndarray, offset: int, direction: int = -1) -> np.ndarray:
    """One frame of the transition: `old` and `new`, `offset` columns in.

    `offset` is how many of the panel's 128 columns have transitioned so far,
    from 0 (pure `old`) to `WIDTH` (pure `new`). `direction=-1` (the default)
    moves content right-to-left -- the new view enters from the right edge as
    the old view exits to the left, matching a left-to-right reading eye
    picking up new content on the leading (right) side. `direction=1` enters
    from the left instead.

    Both arrays must be the same `(PAGES, WIDTH)` shape `Compositor.fb` uses;
    the result is a new array of that shape, never a view into either input,
    so a caller can safely keep using `old`/`new` afterwards.
    """
    offset = max(0, min(WIDTH, offset))
    if direction < 0:
        return np.concatenate([old[:, offset:], new[:, :offset]], axis=1)
    return np.concatenate([new[:, WIDTH - offset :], old[:, : WIDTH - offset]], axis=1)


class Slide:
    """Stateful wrapper: an in-progress slide from `old` to `new`.

    `compose(out, t)` writes one frame for elapsed time `t` and reports
    whether the slide is still running -- the same shape as
    `effects.Effect.update()`, so a caller already used to that pattern needs
    nothing new here.
    """

    def __init__(self, old: np.ndarray, new: np.ndarray, *, duration: float = 0.3,
                 direction: int = -1):
        if duration <= 0:
            raise ValueError(f"duration must be positive, got {duration}")
        self.old = old
        self.new = new
        self.duration = duration
        self.direction = direction

    def offset(self, t: float) -> int:
        """Columns transitioned at elapsed time `t`, clamped to [0, WIDTH]."""
        progress = min(max(t / self.duration, 0.0), 1.0)
        return round(progress * WIDTH)

    def compose(self, out: np.ndarray, t: float) -> bool:
        """Write one frame into `out`. Returns True while still running.

        At `t >= duration` this writes a frame byte-identical to `new` --
        deliberately, since the Compositor diffs `out` against what it last
        pushed, and a final frame that differs from `new` by even one byte
        would force a second, wasted full-width push right after the slide
        already finished visually.
        """
        out[...] = slide(self.old, self.new, self.offset(t), self.direction)
        return t < self.duration

    def retarget(self, new: np.ndarray) -> None:
        """Replace the incoming frame without restarting progress.

        The incoming view's pixels are captured once, at transition start;
        if its data changes mid-slide (a producer poll lands, say), the old
        `new` would still be what gets slid in -- up to `duration` seconds
        stale by the time it's fully on screen. Progress is a pure function
        of elapsed time, not of `new` itself, so swapping `new` here costs
        nothing beyond the render that produced the replacement.
        """
        self.new = new
