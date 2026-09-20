"""Threshold alert evaluation: firing, hysteresis, hold timers, priority."""

import inspect
import re

from oled_hud.hud.alerts import Alert, Alerts, Rule, default_rules
from oled_hud.hud.producers import default_producers
from oled_hud.hud.store import Reading

NOW = 1000.0


def snap(**values):
    return {k: Reading(k, v, NOW, 60.0) for k, v in values.items()}


class FakeClock:
    """Mutable now(), matching test_producers.py's pattern: `clock.t += 1.0`
    steps time without a real sleep."""

    def __init__(self, t: float = NOW):
        self.t = t

    def __call__(self) -> float:
        return self.t


# -- firing thresholds --------------------------------------------------

def test_a_value_above_the_threshold_fires():
    rule = Rule("cpu.temp", "CPU HOT", above=70.0)
    alerts = Alerts([rule], now=lambda: NOW)
    assert alerts.top(snap(**{"cpu.temp": 71.0}), NOW) is not None


def test_a_value_at_or_below_the_threshold_does_not_fire():
    rule = Rule("cpu.temp", "CPU HOT", above=70.0)
    alerts = Alerts([rule], now=lambda: NOW)
    assert alerts.top(snap(**{"cpu.temp": 70.0}), NOW) is None
    assert alerts.top(snap(**{"cpu.temp": 69.9}), NOW) is None


def test_a_below_rule_fires_under_the_threshold():
    rule = Rule("disk.free_gb", "DISK LOW", below=1.0)
    alerts = Alerts([rule], now=lambda: NOW)
    assert alerts.top(snap(**{"disk.free_gb": 0.5}), NOW) is not None
    assert alerts.top(snap(**{"disk.free_gb": 1.0}), NOW) is None


# -- staleness ------------------------------------------------------------

def test_a_stale_reading_never_fires():
    rule = Rule("cpu.temp", "CPU HOT", above=70.0)
    alerts = Alerts([rule], now=lambda: NOW)
    stale = {"cpu.temp": Reading("cpu.temp", 99.0, NOW - 1000.0, ttl=8.0)}
    assert alerts.top(stale, NOW) is None


def test_a_missing_key_never_fires():
    rule = Rule("cpu.temp", "CPU HOT", above=70.0)
    alerts = Alerts([rule], now=lambda: NOW)
    assert alerts.top({}, NOW) is None


def test_an_alert_clears_when_its_reading_goes_stale():
    # A dead sensor must not leave its alarm stuck on -- see module docstring
    # on why this is the honest tradeoff rather than a bug to route around.
    rule = Rule("cpu.temp", "CPU HOT", above=70.0)
    clock = FakeClock()
    alerts = Alerts([rule], now=clock)
    assert alerts.top(snap(**{"cpu.temp": 90.0}), clock.t) is not None
    stale = {"cpu.temp": Reading("cpu.temp", 90.0, clock.t, ttl=8.0)}
    clock.t += 100.0
    assert alerts.top(stale, clock.t) is None


# -- hysteresis -----------------------------------------------------------

def test_an_alert_holds_until_the_clear_threshold():
    rule = Rule("cpu.temp", "CPU HOT", above=70.0, clear=65.0)
    alerts = Alerts([rule], now=lambda: NOW)
    assert alerts.top(snap(**{"cpu.temp": 71.0}), NOW) is not None
    assert alerts.top(snap(**{"cpu.temp": 66.0}), NOW) is not None  # between 65 and 70
    assert alerts.top(snap(**{"cpu.temp": 64.0}), NOW) is None      # below clear


def test_a_value_between_fire_and_clear_does_not_reset_the_alert():
    rule = Rule("cpu.temp", "CPU HOT", above=70.0, clear=65.0)
    alerts = Alerts([rule], now=lambda: NOW)
    alerts.top(snap(**{"cpu.temp": 80.0}), NOW)
    alerts.top(snap(**{"cpu.temp": 67.0}), NOW)
    alerts.top(snap(**{"cpu.temp": 67.0}), NOW)
    assert alerts.top(snap(**{"cpu.temp": 67.0}), NOW) is not None


def test_a_rule_without_an_explicit_clear_uses_the_fire_threshold():
    rule = Rule("cpu.temp", "CPU HOT", above=70.0)  # no clear=
    alerts = Alerts([rule], now=lambda: NOW)
    alerts.top(snap(**{"cpu.temp": 80.0}), NOW)
    assert alerts.top(snap(**{"cpu.temp": 70.0}), NOW) is None


# -- for_s hold timer -------------------------------------------------------

def test_a_brief_spike_does_not_fire():
    rule = Rule("load.1", "LOAD", above=4.0, for_s=30.0)
    clock = FakeClock()
    alerts = Alerts([rule], now=clock)
    assert alerts.top(snap(**{"load.1": 5.0}), clock.t) is None
    clock.t += 10.0
    assert alerts.top(snap(**{"load.1": 5.0}), clock.t) is None


def test_a_sustained_spike_fires_after_the_hold():
    rule = Rule("load.1", "LOAD", above=4.0, for_s=30.0)
    clock = FakeClock()
    alerts = Alerts([rule], now=clock)
    assert alerts.top(snap(**{"load.1": 5.0}), clock.t) is None
    clock.t += 30.0
    assert alerts.top(snap(**{"load.1": 5.0}), clock.t) is not None


def test_the_hold_timer_resets_when_the_value_drops():
    rule = Rule("load.1", "LOAD", above=4.0, for_s=30.0)
    clock = FakeClock()
    alerts = Alerts([rule], now=clock)
    alerts.top(snap(**{"load.1": 5.0}), clock.t)
    clock.t += 20.0
    alerts.top(snap(**{"load.1": 1.0}), clock.t)  # drops below threshold
    clock.t += 20.0  # 20s since the drop, well under for_s=30 if it hadn't reset
    assert alerts.top(snap(**{"load.1": 5.0}), clock.t) is None


def test_the_alert_since_time_is_when_the_condition_started_not_when_it_fired():
    rule = Rule("load.1", "LOAD", above=4.0, for_s=30.0)
    clock = FakeClock()
    alerts = Alerts([rule], now=clock)
    alerts.top(snap(**{"load.1": 5.0}), clock.t)
    started = clock.t
    clock.t += 30.0
    alert = alerts.top(snap(**{"load.1": 5.0}), clock.t)
    assert alert.since == started


# -- priority and ordering -------------------------------------------------

def test_the_highest_priority_alert_wins():
    hot = Rule("cpu.temp", "CPU HOT", above=70.0, priority=3)
    load = Rule("load.1", "LOAD", above=4.0, priority=0)
    alerts = Alerts([load, hot], now=lambda: NOW)
    top = alerts.top(snap(**{"cpu.temp": 90.0, "load.1": 10.0}), NOW)
    assert top.rule is hot


def test_equal_priority_breaks_by_rule_order():
    a = Rule("cpu.temp", "A", above=70.0, priority=1)
    b = Rule("load.1", "B", above=4.0, priority=1)
    alerts = Alerts([a, b], now=lambda: NOW)
    top = alerts.top(snap(**{"cpu.temp": 90.0, "load.1": 10.0}), NOW)
    assert top.rule is a


def test_evaluate_returns_every_firing_alert_not_just_the_top():
    a = Rule("cpu.temp", "A", above=70.0, priority=1)
    b = Rule("load.1", "B", above=4.0, priority=0)
    alerts = Alerts([a, b], now=lambda: NOW)
    fired = alerts.evaluate(snap(**{"cpu.temp": 90.0, "load.1": 10.0}), NOW)
    assert [f.rule for f in fired] == [a, b]


def test_no_alerts_firing_returns_an_empty_list():
    alerts = Alerts([Rule("cpu.temp", "CPU HOT", above=70.0)], now=lambda: NOW)
    assert alerts.evaluate(snap(**{"cpu.temp": 10.0}), NOW) == []


# -- default rules cross-checked against real producers ---------------------

def _published_keys() -> set[str]:
    """Every dotted store key any default producer's poll() can return, by
    scanning its source for the same "domain.metric" literals views.py reads
    -- without calling poll() itself, which would need real /proc and /sys
    files this test doesn't have on a non-Pi machine.
    """
    key_re = re.compile(r'"([a-z_]\w*\.\w+)"')
    keys: set[str] = set()
    for producer in default_producers():
        src = inspect.getsource(type(producer).poll)
        keys.update(key_re.findall(src))
    return keys


def test_every_default_rule_names_a_key_a_producer_publishes():
    # Catches a renamed store key at test time instead of as a silently-
    # never-firing alarm -- default_rules() and producers.py are otherwise
    # two files with no import relationship enforcing they agree.
    published = _published_keys()
    for rule in default_rules():
        assert rule.key in published, f"{rule.key!r} is not published by any default producer"


def test_default_rules_have_unique_keys():
    keys = [r.key for r in default_rules()]
    assert len(keys) == len(set(keys))
