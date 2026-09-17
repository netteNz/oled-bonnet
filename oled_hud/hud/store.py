"""Latest-value store shared between producer threads and the render loop
(Phase H1).

One value per key, always the newest -- no history, no queue. A HUD only
ever draws the current reading, so a ring buffer would just be a slower way
to reach the same byte (the same reasoning that made `LatestBlock` the right
shape for the audio demos).

Two properties the render loop depends on:

* `snapshot()` copies every entry under a single lock acquire, so a view can
  never draw half of one producer's update next to half of the next.
* Staleness is derived on *read* from the entry's TTL, not written by the
  producer. A producer that dies stops refreshing its key and the value ages
  out on its own -- nothing has to notice the death and mark anything. The
  view then shows "--" instead of a frozen number that still looks live,
  which is the failure this class exists to make impossible.
"""

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    """One stored value plus when it landed. `now` is passed in rather than
    read inside, so a whole snapshot is judged fresh or stale against one
    consistent instant instead of the clock moving between fields.
    """

    key: str
    value: object
    t: float
    ttl: float

    def age(self, now: float) -> float:
        return now - self.t

    def fresh(self, now: float) -> bool:
        return self.ttl <= 0 or (now - self.t) <= self.ttl


class Store:
    """Thread-safe map of key -> Reading.

    `now` is injectable the same way `FrameClock`'s is, so tests can step
    time across a TTL boundary instead of sleeping through it.
    """

    DEFAULT_TTL = 15.0

    def __init__(self, *, now=time.monotonic):
        self._now = now
        self._lock = threading.Lock()
        self._data: dict[str, Reading] = {}
        self._version = 0

    @property
    def version(self) -> int:
        """Bumped on every put. The render loop compares this against what it
        last drew and re-renders only on a change -- cheaper than diffing
        values, and it keeps most frames in the Compositor's push-nothing
        case instead of rebuilding an identical framebuffer 60 times a second.
        """
        with self._lock:
            return self._version

    def put(self, key: str, value, ttl: float | None = None) -> None:
        reading = Reading(key, value, self._now(), self.DEFAULT_TTL if ttl is None else ttl)
        with self._lock:
            self._data[key] = reading
            self._version += 1

    def put_all(self, values: dict, ttl: float | None = None) -> None:
        """Write a producer's whole poll result as one version bump, so the
        render loop never wakes to a partially-applied poll.
        """
        if not values:
            return
        t = self._now()
        ttl = self.DEFAULT_TTL if ttl is None else ttl
        with self._lock:
            for key, value in values.items():
                self._data[key] = Reading(key, value, t, ttl)
            self._version += 1

    def get(self, key: str) -> Reading | None:
        with self._lock:
            return self._data.get(key)

    def snapshot(self) -> dict[str, Reading]:
        """A consistent copy of every entry. Readings are frozen, so the
        shallow copy is safe to hold across the render without a lock.
        """
        with self._lock:
            return dict(self._data)

    def now(self) -> float:
        return self._now()
