import pytest

from oled_hud.clock import FrameClock

FPS = 50
PERIOD = 1.0 / FPS


class FakeTime:
    """Deterministic stand-in for perf_counter/sleep, so pacing behaviour can
    be asserted exactly instead of waited on."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.t += duration

    def work(self, duration):
        """Simulate a frame's work taking `duration` seconds."""
        self.t += duration


def make_clock(fps=FPS):
    fake = FakeTime()
    return FrameClock(fps, now=fake.now, sleep=fake.sleep), fake


def test_period_derived_from_fps():
    clock, _ = make_clock(25)
    assert clock.period == pytest.approx(0.04)


@pytest.mark.parametrize("fps", [0, -1, -0.5])
def test_non_positive_fps_raises(fps):
    with pytest.raises(ValueError):
        FrameClock(fps)


def test_tick_sleeps_to_the_deadline():
    clock, fake = make_clock()
    fake.work(PERIOD / 4)
    slack = clock.tick()

    assert slack == pytest.approx(PERIOD * 0.75)
    assert fake.sleeps == [pytest.approx(PERIOD * 0.75)]
    assert fake.t == pytest.approx(PERIOD)


def test_no_cumulative_drift_over_many_frames():
    clock, fake = make_clock()
    # Jittery but always-under-budget work: the whole point is that varying
    # frame cost must not move where frame N's deadline lands.
    for i in range(1000):
        fake.work(PERIOD * (0.1 + 0.7 * ((i * 7) % 10) / 10))
        clock.tick()

    assert fake.t == pytest.approx(1000 * PERIOD, abs=1e-9)
    assert clock.frames == 1000
    assert clock.late == 0
    assert clock.dropped == 0


def test_one_long_frame_does_not_shift_later_deadlines():
    clock, fake = make_clock()
    clock.tick()
    fake.work(PERIOD * 0.9)  # slow frame, still inside budget
    clock.tick()
    fake.work(PERIOD * 0.1)
    clock.tick()

    assert fake.t == pytest.approx(3 * PERIOD)


def test_overrun_reports_negative_slack_and_does_not_sleep():
    clock, fake = make_clock()
    fake.work(PERIOD * 1.5)
    slack = clock.tick()

    assert slack == pytest.approx(-PERIOD * 0.5)
    assert fake.sleeps == []
    assert clock.late == 1
    assert clock.dropped == 0  # behind by less than a full period


def test_large_overrun_drops_frames_and_resyncs():
    clock, fake = make_clock()
    fake.work(PERIOD * 3.5)
    clock.tick()

    assert clock.late == 1
    assert clock.dropped == 2  # 2 whole periods missed beyond the current one

    # After resyncing, the next deadline is less than one period away, so the
    # clock is back in step instead of bursting through the backlog.
    slack = clock.tick()
    assert 0 < slack <= PERIOD
    assert clock.dropped == 2


def test_min_and_mean_slack_track_the_budget():
    clock, fake = make_clock()
    for cost in (PERIOD * 0.25, PERIOD * 0.75, PERIOD * 0.5):
        fake.work(cost)
        clock.tick()

    assert clock.min_slack == pytest.approx(PERIOD * 0.25)
    assert clock.mean_slack == pytest.approx(PERIOD * 0.5)


def test_stats_reports_measured_fps_and_slack():
    clock, fake = make_clock()
    for _ in range(10):
        fake.work(PERIOD * 0.5)
        clock.tick()

    stats = clock.stats()
    assert stats["frames"] == 10
    assert stats["late"] == 0
    assert stats["dropped"] == 0
    assert stats["budget"] == pytest.approx(PERIOD)
    assert stats["elapsed"] == pytest.approx(10 * PERIOD)
    assert stats["actual_fps"] == pytest.approx(FPS)
    assert stats["target_fps"] == FPS
    assert stats["mean_slack"] == pytest.approx(PERIOD * 0.5)
    assert stats["min_slack"] == pytest.approx(PERIOD * 0.5)


def test_stats_on_a_fresh_clock_is_safe():
    clock, _ = make_clock()
    stats = clock.stats()

    assert stats["frames"] == 0
    assert stats["actual_fps"] == 0.0
    assert stats["min_slack"] == 0.0  # not inf
    assert "frames" in clock.summary()


def test_reset_clears_stats_and_rebases_the_origin():
    clock, fake = make_clock()
    fake.work(PERIOD * 2)
    clock.tick()
    assert clock.frames == 1

    fake.work(1.0)
    clock.reset()
    assert clock.frames == 0
    assert clock.late == 0
    assert clock.dropped == 0
    assert clock.elapsed == pytest.approx(0.0)

    fake.work(PERIOD * 0.5)
    assert clock.tick() == pytest.approx(PERIOD * 0.5)


def test_elapsed_tracks_the_time_source():
    clock, fake = make_clock()
    fake.work(2.5)
    assert clock.elapsed == pytest.approx(2.5)
