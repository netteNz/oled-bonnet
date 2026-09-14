"""Frame pacing (Phase 4).

`FrameClock` is a fixed-timestep pacer: it sleeps until each frame's
deadline and reports how much slack was left over. Deadlines come from a
fixed origin (`t0 + n * period`) rather than from a running sum, so a
single long frame doesn't shove every later frame back — there is no
cumulative drift over a 30-minute run, which is what the handoff's
"no drift" acceptance criterion is about.

Slack is the headroom that was left in the frame budget: positive means
the frame's work finished early, negative means it overran. It's returned
from `tick()` and summarized in `stats()` so the budget is a measured
number instead of an assumption.
"""

import time


class FrameClock:
    """Paces a loop at `fps`, without cumulative drift.

    `now`/`sleep` are injectable so tests can drive the clock
    deterministically instead of sleeping in real time.
    """

    def __init__(self, fps: float, *, now=time.perf_counter, sleep=time.sleep):
        if fps <= 0:
            raise ValueError(f"fps must be positive, got {fps}")
        self.fps = fps
        self.period = 1.0 / fps
        self._now = now
        self._sleep = sleep
        self.reset()

    def reset(self) -> None:
        """Restart pacing and clear the collected stats."""
        self._t0 = self._now()
        self._n = 0
        self.frames = 0
        self.late = 0
        self.dropped = 0
        self.min_slack = float("inf")
        self._slack_sum = 0.0

    @property
    def elapsed(self) -> float:
        """Seconds since construction or the last `reset()`."""
        return self._now() - self._t0

    @property
    def mean_slack(self) -> float:
        return self._slack_sum / self.frames if self.frames else 0.0

    def tick(self) -> float:
        """Sleep until the next frame deadline; return this frame's slack.

        If the frame overran by a whole period or more, the missed frames
        are skipped rather than run back-to-back — catching up by bursting
        would make a ticker visibly jump instead of just stutter.
        """
        self._n += 1
        deadline = self._t0 + self._n * self.period
        slack = deadline - self._now()

        if slack > 0:
            self._sleep(slack)
        else:
            self.late += 1
            missed = int(-slack // self.period)
            if missed:
                self._n += missed
                self.dropped += missed

        self.frames += 1
        self.min_slack = min(self.min_slack, slack)
        self._slack_sum += slack
        return slack

    def stats(self) -> dict:
        """Snapshot of pacing behaviour since the last `reset()`."""
        elapsed = self.elapsed
        return {
            "frames": self.frames,
            "late": self.late,
            "dropped": self.dropped,
            "elapsed": elapsed,
            "target_fps": self.fps,
            "actual_fps": self.frames / elapsed if elapsed > 0 else 0.0,
            "budget": self.period,
            "mean_slack": self.mean_slack,
            "min_slack": self.min_slack if self.frames else 0.0,
        }

    def summary(self) -> str:
        """One-line, human-readable form of `stats()`."""
        s = self.stats()
        return (
            f"{s['frames']} frames in {s['elapsed']:.1f}s "
            f"({s['actual_fps']:.1f} fps, target {s['target_fps']:.0f}) | "
            f"budget {s['budget'] * 1e3:.1f}ms, "
            f"slack mean {s['mean_slack'] * 1e3:.1f}ms / "
            f"min {s['min_slack'] * 1e3:.1f}ms | "
            f"late {s['late']}, dropped {s['dropped']}"
        )
