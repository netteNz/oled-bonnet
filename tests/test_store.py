"""Store: TTL staleness, snapshot consistency, versioning, thread safety."""

import threading

import pytest

from oled_hud.hud.store import Reading, Store


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def store(clock):
    return Store(now=clock)


def test_put_then_get(store):
    store.put("a", 1)
    reading = store.get("a")
    assert (reading.key, reading.value) == ("a", 1)


def test_get_missing_key_returns_none(store):
    assert store.get("nope") is None


def test_put_overwrites_rather_than_accumulating(store, clock):
    store.put("a", 1)
    clock.t = 10.0
    store.put("a", 2)
    assert store.get("a").value == 2
    assert store.get("a").t == 10.0


def test_reading_is_fresh_inside_its_ttl(store, clock):
    store.put("a", 1, ttl=5.0)
    clock.t = 4.9
    assert store.get("a").fresh(store.now())


def test_reading_goes_stale_past_its_ttl(store, clock):
    store.put("a", 1, ttl=5.0)
    clock.t = 5.1
    assert not store.get("a").fresh(store.now())


def test_ttl_boundary_is_inclusive(store, clock):
    store.put("a", 1, ttl=5.0)
    clock.t = 5.0
    assert store.get("a").fresh(store.now())


def test_zero_ttl_never_goes_stale(store, clock):
    store.put("a", 1, ttl=0.0)
    clock.t = 1e6
    assert store.get("a").fresh(store.now())


def test_age_reports_elapsed_time(store, clock):
    store.put("a", 1)
    clock.t = 7.5
    assert store.get("a").age(store.now()) == 7.5


def test_default_ttl_is_applied_when_none_given(store):
    store.put("a", 1)
    assert store.get("a").ttl == Store.DEFAULT_TTL


def test_version_starts_at_zero_and_bumps_per_put(store):
    assert store.version == 0
    store.put("a", 1)
    store.put("b", 2)
    assert store.version == 2


def test_put_all_is_a_single_version_bump(store):
    # The render loop wakes on a version change, so a producer's whole poll
    # must land as one change -- otherwise a view can draw half of it.
    store.put_all({"a": 1, "b": 2, "c": 3})
    assert store.version == 1
    assert sorted(store.snapshot()) == ["a", "b", "c"]


def test_put_all_shares_one_timestamp(store):
    store.put_all({"a": 1, "b": 2})
    snap = store.snapshot()
    assert snap["a"].t == snap["b"].t


def test_put_all_with_nothing_is_a_no_op(store):
    store.put("a", 1)
    store.put_all({})
    assert store.version == 1


def test_snapshot_is_a_copy_not_a_live_view(store):
    store.put("a", 1)
    snap = store.snapshot()
    store.put("b", 2)
    assert "b" not in snap


def test_snapshot_readings_are_immutable(store):
    store.put("a", 1)
    reading = store.snapshot()["a"]
    with pytest.raises(Exception):
        reading.value = 2


def test_reading_equality_is_by_value():
    assert Reading("a", 1, 0.0, 5.0) == Reading("a", 1, 0.0, 5.0)


def test_concurrent_writers_leave_no_torn_state(store):
    # Eight threads hammering the same and different keys: every value must
    # be one a writer actually wrote, and the version must equal the number
    # of puts, which only holds if the bump and the write share a lock.
    threads = [
        threading.Thread(target=lambda n=n: [store.put(f"k{n}", i) for i in range(500)])
        for n in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.version == 8 * 500
    snap = store.snapshot()
    assert sorted(snap) == [f"k{n}" for n in range(8)]
    assert all(r.value == 499 for r in snap.values())


def test_snapshot_during_concurrent_writes_stays_consistent(store):
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            store.put_all({"a": i, "b": i})
            i += 1

    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(2000):
            snap = store.snapshot()
            if "a" in snap and "b" in snap:
                # put_all writes both under one lock, so a snapshot can never
                # catch 'a' from one poll next to 'b' from another.
                assert snap["a"].value == snap["b"].value
    finally:
        stop.set()
        t.join()
