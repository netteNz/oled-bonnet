"""Diff/coalesce compositor (Phase H0 of the HUD daemon).

The core everything else in `oled_hud.hud` sits on top of. Widgets write
pixels into `Compositor.fb` by slicing -- page-major MVLSB, the same layout
`pack_bits`/`Tape` already produce. `flush()` diffs `fb` against `pushed`
(the bytes last known to be on the panel) and pushes only the minimum,
choosing per contiguous dirty-page run between one wide push and several
narrow ones by the push-cost model below. Nothing here touches PIL, fonts,
disk or network -- `flush()`'s only I/O is the `driver.blit()` calls it makes.
"""

import numpy as np

PAGES = 4
WIDTH = 128

# BENCH.md, 1MHz partial-blit fit: T(push) ~= FIXED + bytes * PER_BYTE.
# Measurements from the real panel, not tuning knobs -- don't change these
# without rerunning bench.py.
FIXED_MS = 0.6
PER_BYTE_MS = 0.009


def cost(npages: int, ncols: int) -> float:
    """Modeled push time in ms for an `npages` x `ncols` rectangle."""
    return FIXED_MS + npages * ncols * PER_BYTE_MS


def plan_run(dirty: np.ndarray, p0: int, p1: int) -> list[tuple[int, int, int, int]]:
    """Cheapest push plan for one contiguous dirty-page run, pages p0..p1.

    dirty: (PAGES, WIDTH) bool. Returns (page0, page1, col0, col1) tuples:
    either a single push spanning the run's dirty-column union, or one push
    per page narrowed to that page's own dirty columns -- whichever `cost()`
    says is cheaper. A union that's cheap because most of the run really is
    dirty stays one push; a union that's mostly padding around two unrelated
    hot spots loses to two narrow pushes instead.
    """
    cols = np.flatnonzero(dirty[p0 : p1 + 1].any(axis=0))
    union = [(p0, p1, int(cols[0]), int(cols[-1]))]

    per_page = []
    for p in range(p0, p1 + 1):
        c = np.flatnonzero(dirty[p])
        if c.size:
            per_page.append((p, p, int(c[0]), int(c[-1])))

    cu = cost(p1 - p0 + 1, int(cols[-1] - cols[0] + 1))
    cp = sum(cost(1, c1 - c0 + 1) for _, _, c0, c1 in per_page)
    return union if cu <= cp else per_page


class Compositor:
    """Owns the packed framebuffer and the diff against what's on the panel.

    `driver` needs only `blit(data, *, col, ncols, page0, page1)` --
    `PartialSSD1305` or a test double.
    """

    def __init__(self, driver):
        self.driver = driver
        self.fb = np.zeros((PAGES, WIDTH), dtype=np.uint8)
        self.pushed = np.zeros((PAGES, WIDTH), dtype=np.uint8)
        self._force = False

    def force_full(self) -> None:
        """Invalidate the pushed cache so the next flush() pushes all 512
        bytes. Needed after a hardware reset (GDDRAM state is unknown, a
        prior instance may have died mid-blit) and after any burn-in
        column/start-line offset change (H4), since either means `pushed`
        no longer describes what the panel would show for those bytes.
        """
        self._force = True

    def flush(self) -> list[tuple[int, int, int, int]]:
        """Push the minimum bytes needed to make the panel match `fb`.

        Returns the (page0, page1, col0, col1) pushes actually made, for
        instrumentation -- callers can log len(pushes) and total bytes per
        frame.
        """
        if self._force:
            dirty = np.ones((PAGES, WIDTH), dtype=bool)
            self._force = False
        else:
            dirty = self.fb != self.pushed

        page_dirty = dirty.any(axis=1)
        pushes: list[tuple[int, int, int, int]] = []
        p = 0
        while p < PAGES:
            if not page_dirty[p]:
                p += 1
                continue
            q = p
            while q + 1 < PAGES and page_dirty[q + 1]:
                q += 1
            pushes += plan_run(dirty, p, q)
            p = q + 1

        for p0, p1, c0, c1 in pushes:
            data = self.fb[p0 : p1 + 1, c0 : c1 + 1].tobytes()
            self.driver.blit(data, col=c0, ncols=c1 - c0 + 1, page0=p0, page1=p1)

        self.pushed[...] = self.fb
        return pushes
