"""Soak-run instrumentation (closes the last acceptance criterion).

`SoakRecorder` buckets per-frame samples by wall-clock minute and writes one
JSONL line per bucket. Bucketing is the point: a 30-minute run aggregated into
a single mean hides the two things the run exists to find — a slowly climbing
RSS and a drifting latency tail.

Two constraints shape the design:

- **No I/O in the frame path.** `record_frame()` appends to plain lists and
  returns. The only write happens in `maybe_flush()` when a bucket rolls over,
  once a minute rather than 60 times a second.
- **Bounded memory.** At 60fps a bucket holds ~3600 samples; percentiles are
  computed at flush and the raw samples dropped, so the recorder can't itself
  become the leak it is watching for.
"""

import json
import math
import os
import time
from pathlib import Path

PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")


def rss_kb() -> int:
    """Current resident set size in KB, from /proc/self/statm.

    Deliberately not `resource.getrusage().ru_maxrss`: that is a high-water
    mark that never decreases, so it cannot tell a steady leak apart from one
    early allocation spike. statm field 1 is the current resident page count.
    """
    with open("/proc/self/statm") as fh:
        resident_pages = int(fh.read().split()[1])
    return resident_pages * PAGE_SIZE // 1024


def percentile(sorted_values, q: float) -> float:
    """Linear-interpolated percentile of an already-sorted sequence.

    `q` is a fraction (0.95, not 95). Matches numpy's default `linear`
    interpolation so the numbers are comparable to anything computed there.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * q
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(sorted_values[int(k)])
    return float(sorted_values[lo] * (hi - k) + sorted_values[hi] * (k - lo))


class SoakRecorder:
    """Accumulates per-frame samples and flushes per-bucket summaries to JSONL."""

    def __init__(self, path, bucket_s: float = 60.0):
        if bucket_s <= 0:
            raise ValueError(f"bucket_s must be positive, got {bucket_s}")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.bucket_s = bucket_s
        # Line buffering so `tail -f` shows each bucket the moment it lands;
        # at one write per minute the flush costs nothing.
        self._fh = open(self.path, "a", buffering=1)
        self._t0 = None
        self._index = 0
        self._reset_bucket()

    def _reset_bucket(self) -> None:
        self._blit = []
        self._slack = []
        self._frames = 0
        self._late = 0
        self._dropped = 0
        self._errors = 0
        self._error_types = {}

    # -- frame path: append only, never touches the filesystem ----------------

    def record_frame(self, blit_ms: float, slack_ms: float, late: bool, dropped: int) -> None:
        self._blit.append(blit_ms)
        self._slack.append(slack_ms)
        self._frames += 1
        if late:
            self._late += 1
        self._dropped += dropped

    def record_error(self, exc: BaseException) -> None:
        """Count an error by type and errno, so the bucket says what failed."""
        self._errors += 1
        name = type(exc).__name__
        errno = getattr(exc, "errno", None)
        key = f"{name}:{errno}" if errno is not None else name
        self._error_types[key] = self._error_types.get(key, 0) + 1

    # -- bucket rollover ------------------------------------------------------

    def maybe_flush(self, now: float) -> bool:
        """Called once per frame; writes a line only when a bucket rolls over."""
        if self._t0 is None:
            self._t0 = now
            return False

        flushed = False
        # A stall long enough to span several buckets emits an empty line for
        # each one, which is a truer picture than silently widening a bucket.
        while now - self._t0 >= (self._index + 1) * self.bucket_s:
            self._flush_bucket()
            flushed = True
        return flushed

    def _flush_bucket(self) -> None:
        self._fh.write(json.dumps(self._summarize()) + "\n")
        self._index += 1
        self._reset_bucket()

    def _summarize(self) -> dict:
        blit = sorted(self._blit)
        record = {
            "t": self._index * self.bucket_s,
            "frames": self._frames,
            "late": self._late,
            "dropped": self._dropped,
            "errors": self._errors,
            "blit_ms": {
                "p50": round(percentile(blit, 0.50), 3),
                "p95": round(percentile(blit, 0.95), 3),
                "p99": round(percentile(blit, 0.99), 3),
                "max": round(blit[-1], 3) if blit else 0.0,
            },
            "slack_ms": {
                "mean": round(sum(self._slack) / len(self._slack), 3) if self._slack else 0.0,
                "min": round(min(self._slack), 3) if self._slack else 0.0,
            },
            "rss_kb": rss_kb(),
        }
        if self._error_types:
            record["error_types"] = dict(self._error_types)
        return record

    def close(self) -> None:
        """Flush the final partial bucket and close the file."""
        if self._fh.closed:
            return
        if self._frames or self._errors:
            self._flush_bucket()
        self._fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


class BlitTimer:
    """Times just the blit call and counts consecutive I2C failures.

    Counting rather than dying is the point: "3 recovered I2C errors in 30
    minutes" is an input to the HUD daemon's error policy, where a traceback at
    minute 19 is only a lost run. A long unbroken streak is different — that is
    a wedged bus, not a transient, so it re-raises.
    """

    def __init__(self, recorder=None, max_consecutive: int = 50):
        self.recorder = recorder
        self.max_consecutive = max_consecutive
        self.consecutive = 0
        self.errors = 0
        self.blit_ms = 0.0

    def __enter__(self):
        self._t = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, _tb):
        self.blit_ms = (time.perf_counter() - self._t) * 1e3
        if exc_type is None:
            self.consecutive = 0
            return False
        if not issubclass(exc_type, OSError):
            return False
        self.errors += 1
        self.consecutive += 1
        if self.recorder is not None:
            self.recorder.record_error(exc)
        if self.consecutive >= self.max_consecutive:
            return False  # bus is wedged; let it propagate
        return True  # transient: swallow and keep soaking
