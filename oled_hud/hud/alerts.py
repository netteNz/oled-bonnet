"""Threshold alerts over a Store snapshot (Phase H2 preemption).

A rule evaluator, not a `Producer`, on purpose:

* A `Producer`'s contract is "read the world, return values" (`producers.py`).
  Alerts read the *store* instead, which would make `Store` both this
  producer's input and its output, and evaluating on every poll would bump
  `store.version` on every evaluation -- recreating the exact render-gate
  problem H2 exists to solve, just one layer further down.
* Evaluating on the render thread, from the same `Snapshot` the view is
  about to draw, guarantees the alert banner and the number underneath it
  agree. Routed back through the store first, they could disagree by up to
  one poll interval -- exactly the "confident wrong number" `store.py`
  exists to prevent (see its module docstring).
* Rule state -- the `for_s` hold timer, hysteresis -- is scheduler-lifetime
  state, not something a producer's poll-and-forget shape has anywhere to
  keep.

A stale reading never fires, matching `views.fmt()`'s freshness discipline.
This has a real cost, stated rather than hidden: a dead thermal sensor
silently disables the hot-CPU alarm along with the reading it stops
producing. The correct fix is a separate rule keyed on the *absence* of a
fresh reading ("sensor silent"), which is a natural follow-on and
deliberately not built here -- smuggling it in unannounced would hide a
second alert behind the first one's name.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from oled_hud.hud.store import Snapshot

MISSING_PRIORITY = -1


@dataclass(frozen=True)
class Rule:
    """One threshold. Exactly one of `above`/`below` should be set.

    `clear` is the release threshold for hysteresis -- defaults to `above`/
    `below` (no hysteresis) if left `None`. Without it, a reading sitting
    right at the boundary would flap the alert on and off every poll; a gap
    between the fire and clear thresholds means noise smaller than the gap
    can't do that.

    `for_s` is how long the condition must hold, continuously, before the
    rule fires -- a spike that clears on its own within `for_s` never
    reaches the panel. `priority` breaks ties when more than one rule fires
    at once; the scheduler shows only the highest.
    """

    key: str
    label: str
    above: float | None = None
    below: float | None = None
    clear: float | None = None
    spec: str = "{:.1f}"
    priority: int = 0
    for_s: float = 0.0

    def exceeds(self, value: float) -> bool:
        if self.above is not None:
            return value > self.above
        return value < self.below

    def released(self, value: float) -> bool:
        # Inclusive of the boundary itself: `exceeds()` is a strict '>'/'<',
        # so a value sitting exactly on a clear threshold that defaults to
        # the fire threshold is, by definition, no longer exceeding it and
        # must release -- not hold one more tick waiting to go strictly past.
        clear = self.clear if self.clear is not None else (self.above if self.above is not None else self.below)
        if self.above is not None:
            return value <= clear
        return value >= clear


@dataclass(frozen=True)
class Alert:
    """One currently-firing rule, with the value that tripped it and when
    the condition first started (not when it *fired* -- those differ by
    `for_s`), for the alert view's age display.
    """

    rule: Rule
    value: float
    since: float


def default_rules() -> list[Rule]:
    """Sensible defaults for a Pi 3B+, over keys H1's producers already
    publish. `load.1 > 4.0` is all four cores saturated; `for_s = 30`
    is what separates a build from a runaway. Tunable without touching
    `Alerts` -- these are data, not logic.
    """
    return [
        Rule("cpu.temp", "CPU HOT", above=70.0, clear=65.0, priority=3),
        Rule("disk.pct", "DISK FULL", above=90.0, clear=85.0, priority=2),
        Rule("mem.pct", "MEM", above=92.0, clear=88.0, priority=1, for_s=10.0),
        Rule("load.1", "LOAD", above=4.0, clear=3.0, priority=0, for_s=30.0),
    ]


class Alerts:
    """Evaluates a fixed set of `Rule`s against a `Snapshot`, tracking each
    rule's hold timer and firing state across calls.

    `now` is injectable the same way `Store`'s and `FrameClock`'s are, so
    tests can step time across a `for_s` hold without sleeping through it.
    """

    def __init__(self, rules: list[Rule] | None = None, *,
                 now: Callable[[], float] = time.monotonic):
        self.rules = default_rules() if rules is None else list(rules)
        self._now = now
        # Per-rule: when the condition started holding (None if not currently
        # exceeding), and whether it has already fired (stays fired through
        # the hysteresis gap until `released()`).
        self._since: dict[str, float | None] = {r.key: None for r in self.rules}
        self._firing: dict[str, bool] = {r.key: False for r in self.rules}

    def evaluate(self, snap: Snapshot, now: float) -> list[Alert]:
        """Advance every rule's state against `snap` and return the alerts
        currently firing, highest `priority` first (ties broken by the
        order `rules` was given in).
        """
        out = []
        for rule in self.rules:
            reading = snap.get(rule.key)
            if reading is None or not reading.fresh(now):
                # A stale or missing reading can neither start nor continue
                # a hold, and clears any alert already firing -- showing a
                # frozen alarm off a dead sensor is worse than showing none.
                self._since[rule.key] = None
                self._firing[rule.key] = False
                continue

            value = reading.value
            if self._firing[rule.key]:
                if rule.released(value):
                    self._firing[rule.key] = False
                    self._since[rule.key] = None
                else:
                    out.append(Alert(rule, value, self._since[rule.key]))
                continue

            if not rule.exceeds(value):
                self._since[rule.key] = None
                continue

            if self._since[rule.key] is None:
                self._since[rule.key] = now
            held = now - self._since[rule.key]
            if held >= rule.for_s:
                self._firing[rule.key] = True
                out.append(Alert(rule, value, self._since[rule.key]))

        out.sort(key=lambda a: (-a.rule.priority, self.rules.index(a.rule)))
        return out

    def top(self, snap: Snapshot, now: float) -> Alert | None:
        """The single highest-priority alert, or None if nothing is firing.
        What the scheduler actually needs -- the panel shows one at a time.
        """
        alerts = self.evaluate(snap, now)
        return alerts[0] if alerts else None
