"""Local-system producers and the thread that runs them (Phase H1).

Everything here reads this Pi: sysfs and /proc, plus two `os`/`socket`
calls. No network, no credentials, no config file outside the repo -- the
remote-telemetry path already exists separately in `scripts/prom_pull.py`
and `oled_hud/demos/telemetry_display.py`, and a Prometheus producer drops
into the same protocol later without this module changing shape.

Parsing is split out into module-level functions that take text rather than
paths, so they can be tested against captured /proc fixtures instead of
against whatever this machine happens to be doing at test time.

One thread runs every producer, not one thread each: these are
microsecond-scale sysfs reads, so per-producer threads would buy no
concurrency and cost a stack and a wakeup each, all contending with the
frame loop for the GIL. The thread sleeps until the earliest due poll and
handles whatever is due when it wakes.
"""

import os
import socket
import threading
import time
from collections.abc import Callable, Iterable

from oled_hud.hud.store import Store

# Picking a route for a UDP socket costs no packets, so this address never
# has to be reachable -- TEST-NET-1 (RFC 5737) is used precisely because
# nothing will ever answer on it.
_ROUTE_PROBE = ("192.0.2.1", 9)

# A reading is kept for this many polls' worth of time before it ages out.
# One missed poll is a hiccup and shouldn't blank a field; four in a row is a
# dead producer, which is exactly what the store's TTL exists to surface.
TTL_INTERVALS = 4

# How long poll_due() says to sleep when there is nothing to poll at all.
# Only reachable with an empty producer list, which is constructible through
# the public `producers=` argument.
IDLE_INTERVAL = 1.0


def parse_temp(text: str) -> float:
    """thermal_zone temp is millidegrees C."""
    return int(text.strip()) / 1000.0


def parse_cpu_times(text: str) -> tuple[int, int]:
    """First line of /proc/stat -> (busy, total) jiffies.

    idle+iowait counts as idle: a core blocked on I/O isn't doing work, and
    counting iowait as busy makes a quiet Pi with a slow SD card read as
    loaded.
    """
    fields = [int(v) for v in text.splitlines()[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    total = sum(fields[:8])
    return total - idle, total


def parse_meminfo(text: str) -> tuple[float, float]:
    """/proc/meminfo -> (used_mb, total_mb), using MemAvailable.

    MemAvailable, not MemFree: free memory on Linux is mostly page cache the
    kernel will hand back on demand, so MemFree would show this Pi as nearly
    full while it is nearly idle.

    MemAvailable is required, not optional -- unlike `parse_cpu_times`, which
    tolerates a short field list. A kernel too old to publish it (pre-3.14)
    would need MemFree, and MemFree is the wrong number; raising here sends
    the memory row through the producer-error path to "--", which is a
    truthful display, where MemFree would be a confident wrong one.
    """
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(rest.split()[0])  # kB
        if len(values) == 2:
            break
    total = values["MemTotal"] / 1024.0
    return total - values["MemAvailable"] / 1024.0, total


def parse_uptime(text: str) -> float:
    return float(text.split()[0])


def format_uptime(seconds: float) -> str:
    """Compact, fixed-ish width: 45s / 12m / 5h19m / 3d4h."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{s % 3600 // 60:02d}m"
    return f"{s // 86400}d{s % 86400 // 3600:02d}h"


class Producer:
    """Base for every producer: a name, a cadence, and the TTL rule.

    `ttl` is derived rather than restated per producer so the four-intervals
    rule is written down once and a new producer inherits it. Override the
    property if a producer genuinely needs a different ratio.
    """

    name = ""
    interval = 60.0

    @property
    def ttl(self) -> float:
        return TTL_INTERVALS * self.interval

    def poll(self) -> dict[str, object]:
        raise NotImplementedError


class _FileProducer(Producer):
    """Shared plumbing: a producer that reads one text file."""

    path = ""

    def __init__(self, path: str | None = None):
        if path is not None:
            self.path = path

    def read(self) -> str:
        with open(self.path) as fh:
            return fh.read()


class CpuTemp(_FileProducer):
    name = "cputemp"
    interval = 2.0
    path = "/sys/class/thermal/thermal_zone0/temp"

    def poll(self) -> dict[str, object]:
        return {"cpu.temp": parse_temp(self.read())}


class CpuUsage(_FileProducer):
    """Busy percentage over the interval between polls.

    Stateful by nature: /proc/stat is a monotonic counter, so a single read
    gives the average since boot, which on a Pi that has been up for hours is
    a number that never visibly moves. The first poll therefore publishes
    nothing and only establishes the baseline.
    """

    name = "cpuusage"
    interval = 2.0
    path = "/proc/stat"

    def __init__(self, path: str | None = None):
        super().__init__(path)
        self._prev: tuple[int, int] | None = None

    def poll(self) -> dict[str, object]:
        busy, total = parse_cpu_times(self.read())
        prev, self._prev = self._prev, (busy, total)
        if prev is None:
            return {}
        d_busy, d_total = busy - prev[0], total - prev[1]
        if d_total <= 0:
            return {}
        return {"cpu.usage": 100.0 * d_busy / d_total}


class Memory(_FileProducer):
    name = "memory"
    interval = 5.0
    path = "/proc/meminfo"

    def poll(self) -> dict[str, object]:
        used, total = parse_meminfo(self.read())
        return {
            "mem.used_mb": used,
            "mem.total_mb": total,
            "mem.pct": 100.0 * used / total if total else 0.0,
        }


class Uptime(_FileProducer):
    name = "uptime"
    interval = 10.0
    path = "/proc/uptime"

    def poll(self) -> dict[str, object]:
        return {"sys.uptime": parse_uptime(self.read())}


class LoadAvg(Producer):
    name = "loadavg"
    interval = 5.0

    def poll(self) -> dict[str, object]:
        one, five, fifteen = os.getloadavg()
        return {"load.1": one, "load.5": five, "load.15": fifteen}


class Disk(Producer):
    name = "disk"
    interval = 60.0

    def __init__(self, path: str = "/"):
        self.path = path

    def poll(self) -> dict[str, object]:
        st = os.statvfs(self.path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        return {
            "disk.free_gb": free / 2**30,
            "disk.total_gb": total / 2**30,
            "disk.pct": 100.0 * (total - free) / total if total else 0.0,
        }


class Host(Producer):
    """Hostname and the source address of the default route."""

    name = "host"
    interval = 60.0

    def poll(self) -> dict[str, object]:
        out = {"sys.host": socket.gethostname()}
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(_ROUTE_PROBE)
            out["net.ip"] = sock.getsockname()[0]
        except OSError:
            pass  # no route configured; leave net.ip to age out of the store
        finally:
            sock.close()
        return out


def default_producers() -> list[Producer]:
    return [CpuTemp(), CpuUsage(), Memory(), Uptime(), LoadAvg(), Disk(), Host()]


class ProducerThread:
    """Runs producers on their own cadences and writes results into a Store.

    A producer that raises is counted and skipped, never fatal: its last good
    value stays in the store and ages out via its TTL, so a broken sensor
    degrades one field to "--" instead of taking the daemon down. Same posture
    as the poll-failure handling in `demos/telemetry_display.py`, generalized.
    """

    def __init__(self, store: Store, producers: Iterable[Producer] | None = None,
                 *, now: Callable[[], float] = time.monotonic):
        self.store = store
        self.producers = default_producers() if producers is None else list(producers)
        self._now = now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.polls = 0
        self.errors = 0
        self.last_error: str | None = None
        # Keyed by position, not by name: two producers sharing a name would
        # otherwise collapse into one entry and leave one of them never
        # polled again. Everything is due immediately, so the first frame has
        # real data.
        self._due = [0.0] * len(self.producers)

    def poll_due(self) -> float:
        """Poll whatever is due now; return the seconds until the next one.

        Split out from the run loop so a test can step it by hand without a
        thread or a sleep.
        """
        if not self._due:
            return IDLE_INTERVAL
        now = self._now()
        for i, producer in enumerate(self.producers):
            if self._due[i] > now:
                continue
            try:
                values = producer.poll()
                self.polls += 1
            except Exception as exc:  # a bad sensor must not stop the others
                self.errors += 1
                self.last_error = f"{producer.name}: {exc}"
                values = None
            if values:
                self.store.put_all(values, ttl=producer.ttl)
            self._due[i] = now + producer.interval
        return max(0.0, min(self._due) - self._now())

    def _run(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self.poll_due())

    def start(self) -> "ProducerThread":
        self._thread = threading.Thread(target=self._run, name="producers", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()  # wakes the wait() immediately, no sleep to ride out
        if self._thread is not None:
            self._thread.join(timeout)

    def summary(self) -> str:
        tail = f", last error: {self.last_error}" if self.last_error else ""
        return f"producers: {self.polls} polls, {self.errors} errors{tail}"
