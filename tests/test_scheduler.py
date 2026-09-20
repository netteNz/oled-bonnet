"""H2 scheduler: rotation, dwell, alert preemption, and slide transitions."""

import numpy as np
import pytest

from oled_hud.hud import PAGES, WIDTH
from oled_hud.hud.alerts import Alerts, Rule
from oled_hud.hud.font import load
from oled_hud.hud.scheduler import Scheduler
from oled_hud.hud.store import Store
from oled_hud.hud.views import AlertView, View


class FakeClock:
    """Mutable now(), matching test_producers.py's/test_alerts.py's pattern."""

    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeView(View):
    """Fills the whole framebuffer with a single distinguishing byte value,
    or -- if `key` is given -- with whatever the named store reading holds,
    so a test can tell at a glance which view (or which value) is on screen
    without decoding real pixels."""

    def __init__(self, value: int, *, name: str = "fake", refresh: float = 0.0,
                 key: str | None = None):
        self.name = name
        self.refresh = refresh
        self.value = value
        self.key = key
        self.render_calls = 0

    def render_into(self, fb: np.ndarray, snap, now: float) -> None:
        self.render_calls += 1
        if self.key is not None and self.key in snap:
            fb[...] = int(snap[self.key].value) & 0xFF
        else:
            fb[...] = self.value


class CountingStore(Store):
    """A real Store that counts snapshot() calls -- the expensive one."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.snapshot_calls = 0

    def snapshot(self):
        self.snapshot_calls += 1
        return super().snapshot()


def new_fb() -> np.ndarray:
    return np.zeros((PAGES, WIDTH), dtype=np.uint8)


# -- basic rotation -----------------------------------------------------

def test_the_first_view_is_shown_immediately():
    clock = FakeClock()
    store = CountingStore(now=clock)
    view = FakeView(0x11)
    sched = Scheduler([view], now=clock)
    fb = new_fb()
    assert sched.frame(fb, store, clock.t) is True
    assert (fb == 0x11).all()
    assert sched.current is view


def test_an_idle_scheduler_renders_nothing():
    clock = FakeClock()
    store = CountingStore(now=clock)
    sched = Scheduler([FakeView(0x11)], dwell=1000.0, now=clock)
    fb = new_fb()
    sched.frame(fb, store, clock.t)  # the one real render
    for _ in range(50):
        clock.t += 1.0
        assert sched.frame(fb, store, clock.t) is False


def test_a_view_holds_for_its_dwell():
    clock = FakeClock()
    store = CountingStore(now=clock)
    v1, v2 = FakeView(0x11), FakeView(0x22)
    sched = Scheduler([v1, v2], dwell=8.0, transition=0, now=clock)
    fb = new_fb()
    sched.frame(fb, store, clock.t)
    assert (fb == 0x11).all()
    clock.t = 7.9
    assert sched.frame(fb, store, clock.t) is False
    assert (fb == 0x11).all()  # still v1
    clock.t = 8.0
    assert sched.frame(fb, store, clock.t) is True
    assert (fb == 0x22).all()  # switched, hard cut (transition=0)


def test_views_rotate_in_order_and_wrap():
    clock = FakeClock()
    store = CountingStore(now=clock)
    views = [FakeView(1), FakeView(2), FakeView(3)]
    sched = Scheduler(views, dwell=1.0, transition=0, now=clock)
    fb = new_fb()
    seen = []
    for i in range(7):
        clock.t = float(i)
        sched.frame(fb, store, clock.t)
        seen.append(int(fb[0, 0]))
    assert seen == [1, 2, 3, 1, 2, 3, 1]


def test_the_dwell_timer_starts_when_the_transition_finishes():
    # If dwell started at the moment a switch is *initiated* rather than
    # when the slide *lands*, the second switch below would be due at
    # t=2.0 instead of t=2.3 -- catching that off-by-transition-duration
    # bug is the whole point of this test.
    clock = FakeClock()
    store = CountingStore(now=clock)
    views = [FakeView(1), FakeView(2)]
    sched = Scheduler(views, dwell=1.0, transition=0.3, now=clock)
    fb = new_fb()

    clock.t = 0.0
    sched.frame(fb, store, clock.t)  # hard cut, first view, dwell_until = 1.0

    clock.t = 1.0
    sched.frame(fb, store, clock.t)  # dwell expires -> slide starts
    clock.t = 1.3
    sched.frame(fb, store, clock.t)  # slide lands -> dwell_until = 1.3 + 1.0

    clock.t = 2.0
    assert sched.frame(fb, store, clock.t) is False  # not yet due
    clock.t = 2.3
    assert sched.frame(fb, store, clock.t) is True  # now it is


def test_a_single_view_cannot_switch_to_itself():
    # Rotation with one view: dwell expiring must not manufacture a switch
    # (and therefore not a slide, and not an extra render) against itself.
    clock = FakeClock()
    store = CountingStore(now=clock)
    sched = Scheduler([FakeView(0x11)], dwell=1.0, transition=0.3, now=clock)
    fb = new_fb()
    sched.frame(fb, store, clock.t)
    clock.t = 5.0
    assert sched.frame(fb, store, clock.t) is False
    assert sched.switches == 0


def test_a_single_static_view_matches_the_h1_render_count():
    # Locks the H1 property this design must not regress: with one static
    # view and no alerts/transitions, renders track version bumps exactly.
    clock = FakeClock()
    store = CountingStore(now=clock)
    sched = Scheduler([FakeView(0x11)], dwell=100000.0, now=clock)
    fb = new_fb()
    bumps = 0
    for i in range(1800):
        clock.t = i / 60.0
        if i % 90 == 3:  # 19 bumps across 1800 frames, like the H1 measurement
            store.put("x", float(i))
            bumps += 1
        sched.frame(fb, store, clock.t)
    assert sched.renders == 1 + bumps  # the initial show, plus each bump


def test_a_view_with_zero_refresh_renders_only_on_a_version_bump():
    clock = FakeClock()
    store = CountingStore(now=clock)
    sched = Scheduler([FakeView(0x11, refresh=0.0)], dwell=100000.0, now=clock)
    fb = new_fb()
    sched.frame(fb, store, clock.t)
    for i in range(1, 300):
        clock.t = i / 60.0
        sched.frame(fb, store, clock.t)
    assert sched.renders == 1


def test_a_one_hertz_view_renders_once_a_second_not_every_frame():
    clock = FakeClock()
    store = CountingStore(now=clock)
    sched = Scheduler([FakeView(0x11, refresh=1.0)], dwell=100000.0, now=clock)
    fb = new_fb()
    frames = 180  # 3 seconds at 60fps
    for i in range(frames):
        clock.t = i / 60.0
        sched.frame(fb, store, clock.t)
    assert 2 <= sched.renders <= 4  # ~once a second, nowhere near every frame


def test_the_scheduler_only_snapshots_when_it_renders():
    clock = FakeClock()
    store = CountingStore(now=clock)
    sched = Scheduler([FakeView(0x11)], dwell=100000.0, now=clock)
    fb = new_fb()
    sched.frame(fb, store, clock.t)
    assert store.snapshot_calls == 1
    for i in range(1, 200):
        clock.t = i / 60.0
        sched.frame(fb, store, clock.t)
    assert store.snapshot_calls == 1  # nothing changed; never asked again


# -- transitions ----------------------------------------------------------

def test_a_switch_starts_a_slide():
    clock = FakeClock()
    store = CountingStore(now=clock)
    v1, v2 = FakeView(0x11), FakeView(0x22)
    sched = Scheduler([v1, v2], dwell=1.0, transition=0.3, now=clock)
    fb = new_fb()
    sched.frame(fb, store, clock.t)
    clock.t = 1.0
    sched.frame(fb, store, clock.t)
    clock.t = 1.15  # mid-slide
    sched.frame(fb, store, clock.t)
    values = set(np.unique(fb))
    assert values == {0x11, 0x22}  # neither pure old nor pure new


def test_every_frame_of_a_slide_renders():
    clock = FakeClock()
    store = CountingStore(now=clock)
    sched = Scheduler([FakeView(0x11), FakeView(0x22)], dwell=1.0, transition=0.3, now=clock)
    fb = new_fb()
    sched.frame(fb, store, clock.t)
    clock.t = 1.0
    assert sched.frame(fb, store, clock.t) is True
    for t in (1.05, 1.1, 1.2, 1.25):
        clock.t = t
        assert sched.frame(fb, store, clock.t) is True


def test_the_frame_after_a_slide_equals_the_incoming_view():
    clock = FakeClock()
    store = CountingStore(now=clock)
    sched = Scheduler([FakeView(0x11), FakeView(0x22)], dwell=1.0, transition=0.3, now=clock)
    fb = new_fb()
    sched.frame(fb, store, clock.t)
    clock.t = 1.0
    sched.frame(fb, store, clock.t)
    clock.t = 1.3
    sched.frame(fb, store, clock.t)
    assert (fb == 0x22).all()
    assert sched.current is sched.views[1]


def test_transition_zero_switches_instantly():
    clock = FakeClock()
    store = CountingStore(now=clock)
    sched = Scheduler([FakeView(0x11), FakeView(0x22)], dwell=1.0, transition=0, now=clock)
    fb = new_fb()
    sched.frame(fb, store, clock.t)
    clock.t = 1.0
    sched.frame(fb, store, clock.t)
    assert (fb == 0x22).all()  # no partial blend, no extra frame needed


def test_a_version_bump_mid_slide_retargets_the_incoming_frame():
    clock = FakeClock()
    store = CountingStore(now=clock)
    store.put("x", 1.0)
    v1 = FakeView(0x11)
    v2 = FakeView(0, key="x")  # renders the live value of "x"
    sched = Scheduler([v1, v2], dwell=1.0, transition=0.3, now=clock)
    fb = new_fb()
    sched.frame(fb, store, clock.t)  # shows v1
    clock.t = 1.0
    sched.frame(fb, store, clock.t)  # switch starts, captures x=1.0 into "new"
    clock.t = 1.1
    store.put("x", 42.0)  # data changes mid-slide
    sched.frame(fb, store, clock.t)  # should retarget to the new value
    clock.t = 1.3
    sched.frame(fb, store, clock.t)  # slide lands
    assert (fb == 42).all()  # not the stale 1.0 captured at slide start


# -- alert preemption -------------------------------------------------------

def make_alert_scheduler(clock, **kw):
    store = CountingStore(now=clock)
    v1, v2 = FakeView(0x11, name="v1"), FakeView(0x22, name="v2")
    alerts = Alerts([Rule("x", "X ALERT", above=5.0)], now=clock)
    alert_view = AlertView(load("spleen"))
    sched = Scheduler([v1, v2], alerts=alerts, alert_view=alert_view, now=clock, **kw)
    return store, sched, alert_view


def test_an_alert_preempts_the_rotation():
    clock = FakeClock()
    store, sched, alert_view = make_alert_scheduler(clock, dwell=100.0, transition=0)
    fb = new_fb()
    sched.frame(fb, store, clock.t)
    assert sched.current is sched.views[0]

    store.put("x", 10.0)  # crosses the threshold
    clock.t = 0.1
    sched.frame(fb, store, clock.t)
    assert sched.current is alert_view


def test_the_alert_view_does_not_rotate():
    clock = FakeClock()
    store, sched, alert_view = make_alert_scheduler(clock, dwell=1.0, transition=0)
    fb = new_fb()
    store.put("x", 10.0)
    sched.frame(fb, store, clock.t)
    assert sched.current is alert_view
    for i in range(1, 20):
        clock.t = float(i)  # many dwell periods' worth of time
        store.put("x", 10.0)  # the producer keeps confirming the condition
        sched.frame(fb, store, clock.t)
        assert sched.current is alert_view


def test_rotation_resumes_when_the_alert_clears():
    clock = FakeClock()
    store, sched, alert_view = make_alert_scheduler(clock, dwell=1.0, transition=0, min_alert=0.0)
    fb = new_fb()
    clock.t = 0.0
    sched.frame(fb, store, clock.t)  # v1 shown
    clock.t = 0.5
    sched.frame(fb, store, clock.t)  # dwell not yet due, rotates to v2? no -- 0.5<1.0
    # advance past dwell so the interrupted view is v2, to prove resume
    # picks up wherever it was, not always index 0
    clock.t = 1.0
    sched.frame(fb, store, clock.t)  # switches to v2
    assert sched.current is sched.views[1]

    store.put("x", 10.0)
    clock.t = 1.1
    sched.frame(fb, store, clock.t)
    assert sched.current is alert_view

    clock.t = 1.2
    store.put("x", 0.0)  # clears
    sched.frame(fb, store, clock.t)
    assert sched.current is sched.views[1]  # resumed at v2, not v1


def test_an_alert_is_shown_for_at_least_min_alert():
    clock = FakeClock()
    store, sched, alert_view = make_alert_scheduler(
        clock, dwell=100.0, transition=0, min_alert=5.0
    )
    fb = new_fb()
    sched.frame(fb, store, clock.t)
    store.put("x", 10.0)
    clock.t = 0.1
    sched.frame(fb, store, clock.t)
    assert sched.current is alert_view

    store.put("x", 0.0)  # clears almost immediately
    clock.t = 0.2
    sched.frame(fb, store, clock.t)
    assert sched.current is alert_view  # still held by min_alert

    clock.t = 5.1  # min_alert has now elapsed since the alert was shown
    sched.frame(fb, store, clock.t)
    assert sched.current is sched.views[0]


def test_a_flapping_condition_does_not_switch_every_frame():
    clock = FakeClock()
    store, sched, alert_view = make_alert_scheduler(
        clock, dwell=100.0, transition=0, min_alert=2.0
    )
    fb = new_fb()
    sched.frame(fb, store, clock.t)
    for i in range(1, 600):
        clock.t = i * 0.01
        store.put("x", 10.0 if i % 2 == 0 else 0.0)  # oscillates every frame
        sched.frame(fb, store, clock.t)
    assert sched.switches < 10


def test_disabling_alerts_leaves_rotation_untouched():
    clock = FakeClock()
    store = CountingStore(now=clock)
    sched = Scheduler([FakeView(0x11), FakeView(0x22)], dwell=1.0, transition=0, now=clock)
    fb = new_fb()
    sched.frame(fb, store, clock.t)
    clock.t = 1.0
    sched.frame(fb, store, clock.t)
    assert (fb == 0x22).all()


def test_scheduler_requires_at_least_one_view():
    with pytest.raises(ValueError):
        Scheduler([])
