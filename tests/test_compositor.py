import numpy as np
import pytest

from oled_hud.hud.compositor import PAGES, WIDTH, Compositor, cost, plan_run


class FakeDriver:
    """Records blit() calls instead of touching I2C."""

    def __init__(self):
        self.calls = []

    def blit(self, data, *, col, ncols, page0, page1):
        self.calls.append((data, col, ncols, page0, page1))


@pytest.fixture
def driver():
    return FakeDriver()


@pytest.fixture
def comp(driver):
    return Compositor(driver)


def test_no_change_pushes_nothing(comp, driver):
    pushes = comp.flush()
    assert pushes == []
    assert driver.calls == []


def test_one_byte_changed_pushes_one_page_one_column(comp, driver):
    comp.fb[2, 5] = 0xFF
    pushes = comp.flush()
    assert pushes == [(2, 2, 5, 5)]
    assert len(driver.calls) == 1
    data, col, ncols, page0, page1 = driver.calls[0]
    assert (col, ncols, page0, page1) == (5, 1, 2, 2)
    assert data == bytes([0xFF])


def test_non_adjacent_pages_never_coalesced(comp, driver):
    comp.fb[0, 3] = 1
    comp.fb[3, 100] = 1
    pushes = comp.flush()
    assert len(pushes) == 2
    pages_touched = sorted((p0, p1) for p0, p1, _, _ in pushes)
    assert pages_touched == [(0, 0), (3, 3)]


def test_two_pages_fully_dirty_coalesce_to_one_push(comp, driver):
    comp.fb[1:3, :] = 1  # the ticker case: pages 1-2, full width
    pushes = comp.flush()
    assert pushes == [(1, 2, 0, WIDTH - 1)]
    data = driver.calls[0][0]
    assert len(data) == 2 * WIDTH == 256


def test_two_hotspots_in_a_run_prefer_per_page_plan(comp, driver):
    comp.fb[0, 0:11] = 1  # page 0, cols 0-10
    comp.fb[1, 110:128] = 1  # page 1, cols 110-127

    dirty = comp.fb != comp.pushed
    plan = plan_run(dirty, 0, 1)
    assert len(plan) == 2  # cost model must agree per-page beats the union

    pushes = comp.flush()
    assert len(pushes) == 2
    assert sorted(pushes) == [(0, 0, 0, 10), (1, 1, 110, 127)]


def test_force_full_pushes_everything_once(comp, driver):
    comp.force_full()
    pushes = comp.flush()
    assert pushes == [(0, PAGES - 1, 0, WIDTH - 1)]
    data, col, ncols, page0, page1 = driver.calls[0]
    assert (col, ncols, page0, page1) == (0, WIDTH, 0, PAGES - 1)
    assert len(data) == PAGES * WIDTH == 512

    # force_full is one-shot: the next flush with no further changes is quiet.
    driver.calls.clear()
    assert comp.flush() == []
    assert driver.calls == []


@pytest.mark.parametrize("seed", range(20))
def test_pushed_matches_fb_after_any_flush(seed):
    rng = np.random.default_rng(seed)
    driver = FakeDriver()
    comp = Compositor(driver)
    comp.fb[...] = rng.integers(0, 256, size=(PAGES, WIDTH), dtype=np.uint8)
    if rng.random() < 0.3:
        comp.force_full()
    comp.flush()
    assert np.array_equal(comp.pushed, comp.fb)


def test_cost_model_matches_bench_constants():
    assert cost(0, 0) == pytest.approx(0.6)
    assert cost(2, 128) == pytest.approx(0.6 + 2 * 128 * 0.009)


def test_plan_run_never_leaves_dirty_bytes_unpushed():
    rng = np.random.default_rng(0)
    for _ in range(50):
        dirty = rng.random((PAGES, WIDTH)) < 0.1
        if not dirty.any():
            continue
        page_dirty = dirty.any(axis=1)
        p = int(np.flatnonzero(page_dirty)[0])
        q = p
        while q + 1 < PAGES and page_dirty[q + 1]:
            q += 1
        covered = np.zeros_like(dirty)
        for p0, p1, c0, c1 in plan_run(dirty, p, q):
            covered[p0 : p1 + 1, c0 : c1 + 1] = True
        assert np.array_equal(dirty[p : q + 1] & ~covered[p : q + 1], np.zeros((q - p + 1, WIDTH), dtype=bool))
