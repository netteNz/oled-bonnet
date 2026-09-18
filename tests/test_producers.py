"""Local-system producers: parsers against captured fixtures, and the
scheduling/error behaviour of the thread that runs them."""

import threading

import pytest

from oled_hud.hud.producers import (
    IDLE_INTERVAL,
    TTL_INTERVALS,
    CpuTemp,
    CpuUsage,
    Memory,
    ProducerThread,
    Uptime,
    default_producers,
    format_uptime,
    parse_cpu_times,
    parse_meminfo,
    parse_temp,
    parse_uptime,
)
from oled_hud.hud.store import Store

# Captured from this Pi rather than invented, so a kernel that changes the
# column layout shows up here as a test failure instead of a wrong number on
# the panel.
PROC_STAT = """cpu  25616 58 8197 7538889 12265 0 737 0 0 0
cpu0 6110 32 1924 1885233 3065 0 186 0 0 0
intr 12345
"""
MEMINFO = """MemTotal:         926828 kB
MemFree:           54676 kB
MemAvailable:     426740 kB
Buffers:           31032 kB
"""


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def test_parse_temp_converts_millidegrees():
    assert parse_temp("49926\n") == pytest.approx(49.926)


def test_parse_cpu_times_counts_iowait_as_idle():
    busy, total = parse_cpu_times(PROC_STAT)
    fields = [25616, 58, 8197, 7538889, 12265, 0, 737, 0]
    assert total == sum(fields)
    assert busy == total - (7538889 + 12265)


def test_parse_cpu_times_tolerates_a_short_line():
    # Older/simpler /proc/stat without the iowait column onward.
    busy, total = parse_cpu_times("cpu  100 0 50 850\n")
    assert (busy, total) == (150, 1000)


def test_parse_meminfo_uses_memavailable_not_memfree():
    used, total = parse_meminfo(MEMINFO)
    assert total == pytest.approx(926828 / 1024)
    assert used == pytest.approx((926828 - 426740) / 1024)
    # MemFree would claim ~852MB used; MemAvailable says ~488MB.
    assert used < (926828 - 54676) / 1024


def test_parse_uptime_takes_the_first_field():
    assert parse_uptime("19139.46 75388.87\n") == pytest.approx(19139.46)


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0s"), (45, "45s"), (59, "59s"), (60, "1m"), (700, "11m"),
     (3599, "59m"), (3600, "1h00m"), (19139, "5h18m"), (86399, "23h59m"),
     (86400, "1d00h"), (300000, "3d11h")],
)
def test_format_uptime(seconds, expected):
    assert format_uptime(seconds) == expected


def test_cputemp_producer_reads_its_file(tmp_path):
    producer = CpuTemp(write(tmp_path, "temp", "51540\n"))
    assert producer.poll() == {"cpu.temp": pytest.approx(51.54)}


def test_uptime_producer_reads_its_file(tmp_path):
    producer = Uptime(write(tmp_path, "uptime", "123.5 400.0\n"))
    assert producer.poll() == {"sys.uptime": pytest.approx(123.5)}


def test_memory_producer_reports_used_total_and_percent(tmp_path):
    values = Memory(write(tmp_path, "meminfo", MEMINFO)).poll()
    assert values["mem.pct"] == pytest.approx(
        100.0 * values["mem.used_mb"] / values["mem.total_mb"]
    )


def test_cpuusage_first_poll_publishes_nothing(tmp_path):
    # /proc/stat is a since-boot counter, so one read alone can only give
    # the average since boot -- useless on a machine that has been up hours.
    producer = CpuUsage(write(tmp_path, "stat", "cpu  100 0 50 850 0 0 0 0\n"))
    assert producer.poll() == {}


def test_cpuusage_second_poll_reports_the_delta(tmp_path):
    path = write(tmp_path, "stat", "cpu  100 0 50 850 0 0 0 0\n")
    producer = CpuUsage(path)
    producer.poll()
    # +50 busy, +50 idle over the interval -> 50%
    open(path, "w").write("cpu  125 0 75 900 0 0 0 0\n")
    assert producer.poll() == {"cpu.usage": pytest.approx(50.0)}


def test_cpuusage_ignores_a_zero_length_interval(tmp_path):
    path = write(tmp_path, "stat", "cpu  100 0 50 850 0 0 0 0\n")
    producer = CpuUsage(path)
    producer.poll()
    assert producer.poll() == {}  # counters unchanged, no division by zero


class FakeProducer:
    def __init__(self, name, interval, ttl=10.0, values=None, raises=None):
        self.name = name
        self.interval = interval
        self.ttl = ttl
        self._values = {name: 1} if values is None else values
        self._raises = raises
        self.calls = 0

    def poll(self):
        self.calls += 1
        if self._raises:
            raise self._raises
        return dict(self._values)


def test_every_producer_is_due_on_the_first_pass():
    store = Store()
    a, b = FakeProducer("a", 2.0), FakeProducer("b", 60.0)
    ProducerThread(store, [a, b], now=FakeClock()).poll_due()
    assert (a.calls, b.calls) == (1, 1)
    assert sorted(store.snapshot()) == ["a", "b"]


def test_a_producer_is_not_polled_again_before_its_interval():
    clock = FakeClock()
    producer = FakeProducer("a", 10.0)
    thread = ProducerThread(Store(), [producer], now=clock)
    thread.poll_due()
    clock.t = 9.0
    thread.poll_due()
    assert producer.calls == 1
    clock.t = 10.0
    thread.poll_due()
    assert producer.calls == 2


def test_poll_due_returns_the_time_until_the_next_producer():
    clock = FakeClock()
    thread = ProducerThread(Store(), [FakeProducer("a", 2.0), FakeProducer("b", 60.0)], now=clock)
    assert thread.poll_due() == pytest.approx(2.0)
    clock.t = 2.0
    assert thread.poll_due() == pytest.approx(2.0)


def test_producer_ttl_is_carried_into_the_store():
    store = Store()
    ProducerThread(store, [FakeProducer("a", 2.0, ttl=8.0)], now=FakeClock()).poll_due()
    assert store.get("a").ttl == 8.0


def test_a_raising_producer_does_not_stop_the_others():
    store = Store()
    bad = FakeProducer("bad", 1.0, raises=OSError("sensor gone"))
    good = FakeProducer("good", 1.0)
    thread = ProducerThread(store, [bad, good], now=FakeClock())
    thread.poll_due()
    assert good.calls == 1
    assert "good" in store.snapshot()
    assert thread.errors == 1
    assert "sensor gone" in thread.last_error


def test_a_raising_producer_leaves_its_last_good_value_in_place():
    clock = FakeClock()
    store = Store(now=clock)
    producer = FakeProducer("a", 1.0, ttl=5.0)
    thread = ProducerThread(store, [producer], now=clock)
    thread.poll_due()
    producer._raises = OSError("gone")
    clock.t = 1.0
    thread.poll_due()
    # The value survives, but it is now aging out -- which is what turns it
    # into "--" on the panel rather than a number frozen forever.
    assert store.get("a").value == 1
    assert store.get("a").fresh(store.now())
    clock.t = 6.0
    assert not store.get("a").fresh(store.now())


def test_a_raising_producer_is_rescheduled_not_abandoned():
    clock = FakeClock()
    producer = FakeProducer("a", 1.0, raises=OSError("flaky"))
    thread = ProducerThread(Store(), [producer], now=clock)
    thread.poll_due()
    clock.t = 1.0
    thread.poll_due()
    assert producer.calls == 2


def test_a_producer_returning_nothing_bumps_no_version():
    store = Store()
    ProducerThread(store, [FakeProducer("a", 1.0, values={})], now=FakeClock()).poll_due()
    assert store.version == 0


def test_summary_reports_polls_and_errors():
    thread = ProducerThread(Store(), [FakeProducer("a", 1.0)], now=FakeClock())
    thread.poll_due()
    assert "1 polls, 0 errors" in thread.summary()


def test_start_and_stop_runs_a_daemon_thread_and_joins_it():
    store = Store()
    thread = ProducerThread(store, [FakeProducer("a", 0.01)]).start()
    assert thread._thread.daemon  # must not keep the process alive on crash
    for _ in range(200):
        if "a" in store.snapshot():
            break
    thread.stop()
    assert not thread._thread.is_alive()
    assert "a" in store.snapshot()


def test_stop_interrupts_a_long_wait_instead_of_riding_it_out():
    # A 1-hour interval means the thread is parked in wait(); stop() must
    # return promptly, or SIGTERM shutdown would hang for the interval.
    thread = ProducerThread(Store(), [FakeProducer("a", 3600.0)]).start()
    done = threading.Event()
    threading.Thread(target=lambda: (thread.stop(), done.set()), daemon=True).start()
    assert done.wait(2.0)


def test_an_empty_producer_list_idles_instead_of_raising():
    # `producers=` is public, so an empty list is constructible. Everything
    # else in this module degrades rather than raises; min() over an empty
    # _due was the one place that didn't.
    thread = ProducerThread(Store(), [], now=FakeClock())
    assert thread.poll_due() == IDLE_INTERVAL


def test_two_producers_sharing_a_name_both_get_polled():
    # _due used to be keyed by producer.name, so a duplicate name silently
    # collapsed two entries into one and left a producer never polled again.
    clock = FakeClock()
    a = FakeProducer("dupe", 1.0, values={"a": 1})
    b = FakeProducer("dupe", 1.0, values={"b": 2})
    thread = ProducerThread(Store(), [a, b], now=clock)

    thread.poll_due()
    clock.t += 1.0
    thread.poll_due()

    assert (a.calls, b.calls) == (2, 2)


def test_every_default_producer_keeps_four_intervals_of_readings():
    # The TTL rule, asserted rather than restated seven times: a producer has
    # to miss four polls running before its fields go to "--".
    for producer in default_producers():
        assert producer.ttl == TTL_INTERVALS * producer.interval
