"""H2: rotates the daemon among several views, preempts for alerts, and
slides between whichever two are involved.

`Scheduler.frame()` is what the daemon's frame loop calls in place of the H1
`if version != seen: view.render_into(...)` check -- it owns every reason a
frame might need to be redrawn: the data changed, a view's own `refresh`
cadence is due (a clock), a dwell timer expired (rotation), or an alert
appeared or cleared (preemption). Any one of those returns True and the
gate becomes a disjunction rather than the single H1 condition; see the
module docstring in `daemon.py` for why a 1Hz clock view doesn't need to
become a `Producer` to fit into that gate.

Takes the `Store`, not a `Snapshot`: the daemon currently calls
`store.snapshot()` only on a version change, and calling it every frame at
60fps would be 60 locked dict copies a second bought back for nothing --
the scheduler is the thing that decides whether this frame needs data, so
it should be the thing that asks for it. Alert evaluation reads individual
keys with `store.get()` instead, for the same reason one level down: a rule
only needs its own key, not a copy of every reading in the store.
"""

import time
from collections.abc import Callable, Sequence

import numpy as np

from oled_hud.hud.alerts import Alert, Alerts
from oled_hud.hud.transition import Slide
from oled_hud.hud.views import AlertView, View


class Scheduler:
    """Owns the "when" that every `View` in H1/H2 deliberately has no
    opinion about.

    `views` is the rotation, in order; `alert_view`/`alerts` are optional --
    passing neither disables preemption entirely (the `--no-alerts` case),
    and the rotation still runs. `transition <= 0` disables the slide and
    every switch becomes a hard cut, which is also what a brand-new
    scheduler does for its very first frame -- there is nothing to slide
    *from* yet.
    """

    def __init__(self, views: Sequence[View], *, dwell: float = 8.0,
                 alerts: Alerts | None = None, alert_view: AlertView | None = None,
                 transition: float = 0.3, min_alert: float = 5.0,
                 now: Callable[[], float] = time.monotonic):
        if not views:
            raise ValueError("Scheduler needs at least one view")
        self.views = list(views)
        self.alerts = alerts
        self.alert_view = alert_view
        self.dwell = dwell
        self.transition_s = transition
        self.min_alert = min_alert
        # `now` is not read inside frame() -- every timestamp there comes
        # from frame()'s own `now` argument, so one clock read per frame
        # loop iteration feeds both the Compositor's timing and this. Kept
        # as an injectable default anyway, matching Store's and
        # ProducerThread's constructors, for a caller that wants a
        # Scheduler usable on its own without wiring frame() up yet.
        self._now = now

        self._idx = 0
        self._displayed: View | None = None
        self._mode = "rotation"  # or "alert"
        self._dwell_until: float | None = None
        self._refresh_due: float | None = None
        self._alert_shown_at: float | None = None
        self._last_version: int | None = None
        self._last_top: Alert | None = None

        self._slide: Slide | None = None
        self._slide_start = 0.0
        self._slide_target: View | None = None
        self._slide_target_mode = "rotation"

        self.renders = 0
        self.switches = 0
        self.alerts_fired = 0

    @property
    def current(self) -> View:
        """The view actually on screen right now (mid-slide: the outgoing
        one, since that's still what most of the panel shows)."""
        return self._displayed if self._displayed is not None else self.views[self._idx]

    @property
    def alert(self) -> Alert | None:
        """The currently-firing top alert, or None. Read by the daemon to
        decide whether to play an attention effect on alert *start*."""
        return self._last_top

    def summary(self) -> str:
        return f"scheduler: {self.renders} renders, {self.switches} switches, {self.alerts_fired} alerts"

    def frame(self, fb: np.ndarray, store, now: float) -> bool:
        """Advance one frame. Returns True if `fb` was written."""
        version = store.version
        top = self._top_alert(store, now)
        if top is not None and self.alert_view is not None:
            self.alert_view.show(top)
        if top is not self._last_top and top is not None:
            if self._last_top is None or top.rule is not self._last_top.rule:
                self.alerts_fired += 1
        self._last_top = top

        if self._slide is not None:
            return self._continue_slide(fb, store, version, now)

        target, mode = self._pick_target(top is not None, now)
        if target is not None:
            return self._start_switch(fb, store, target, mode, now)

        return self._maybe_plain_render(fb, store, version, now)

    # -- alert lookup ---------------------------------------------------

    def _top_alert(self, store, now: float) -> Alert | None:
        if self.alerts is None or self.alert_view is None:
            return None
        # Only the keys the rules actually use -- store.get() per key rather
        # than store.snapshot(), so evaluating alerts every frame never
        # copies the whole store just to look at three or four readings.
        snap = {}
        for rule in self.alerts.rules:
            reading = store.get(rule.key)
            if reading is not None:
                snap[rule.key] = reading
        return self.alerts.top(snap, now)

    # -- deciding whether/what to switch to ------------------------------

    def _pick_target(self, alert_active: bool, now: float) -> tuple[View | None, str]:
        if alert_active:
            if self._mode != "alert":
                return self.alert_view, "alert"
            return None, "alert"

        if self._mode == "alert":
            held = now - self._alert_shown_at if self._alert_shown_at is not None else self.min_alert
            if held >= self.min_alert:
                return self.views[self._idx], "rotation"
            return None, "alert"

        if self._displayed is None:
            return self.views[self._idx], "rotation"

        if self._dwell_until is not None and now >= self._dwell_until:
            if len(self.views) > 1:
                self._idx = (self._idx + 1) % len(self.views)
                return self.views[self._idx], "rotation"
            self._dwell_until = now + self.dwell  # nothing to rotate to
        return None, "rotation"

    # -- switching, with or without a slide ------------------------------

    def _start_switch(self, fb, store, target: View, mode: str, now: float) -> bool:
        if self._displayed is not None and self._displayed is not target:
            self.switches += 1
        snap = store.snapshot()
        self._last_version = store.version

        if self.transition_s <= 0 or self._displayed is None:
            target.render_into(fb, snap, now)
            self.renders += 1
            self._land(target, mode, now)
            return True

        old = fb.copy()
        new = np.zeros_like(fb)
        target.render_into(new, snap, now)
        self._slide = Slide(old, new, duration=self.transition_s)
        self._slide_start = now
        self._slide_target = target
        self._slide_target_mode = mode
        self._slide.compose(fb, 0.0)
        self.renders += 1
        return True

    def _continue_slide(self, fb, store, version: int, now: float) -> bool:
        if version != self._last_version:
            self._last_version = version
            scratch = np.zeros_like(fb)
            self._slide_target.render_into(scratch, store.snapshot(), now)
            self._slide.retarget(scratch)

        t = now - self._slide_start
        running = self._slide.compose(fb, t)
        self.renders += 1
        if not running:
            target, mode = self._slide_target, self._slide_target_mode
            self._slide = self._slide_target = None
            self._land(target, mode, now)
        return True

    def _land(self, target: View, mode: str, now: float) -> None:
        """Record that `target` is now fully on screen (a switch just
        finished, whether by hard cut or by a slide completing)."""
        self._displayed = target
        self._mode = mode
        self._refresh_due = now + target.refresh if target.refresh > 0 else None
        if mode == "alert":
            self._alert_shown_at = now
        else:
            self._dwell_until = now + self.dwell

    # -- no switch: maybe still a plain re-render ------------------------

    def _maybe_plain_render(self, fb, store, version: int, now: float) -> bool:
        need = version != self._last_version
        if not need and self._refresh_due is not None and now >= self._refresh_due:
            need = True
        if not need:
            return False

        self._last_version = version
        self._displayed.render_into(fb, store.snapshot(), now)
        self.renders += 1
        if self._displayed.refresh > 0:
            self._refresh_due = now + self._displayed.refresh
        return True
