"""HUD views (Phase H1) -- so far exactly one, the local-system view.

A view turns a `Store` snapshot into pixels and nothing else: it owns no
timer, no polling and no panel. That keeps H2's scheduler free to decide
*when* a view is drawn without the view having an opinion, and keeps this
module testable against a dict with no hardware anywhere.

Layout is computed from the font rather than hardcoded, because the two
vendored fonts give different budgets -- Spleen 5x8 fits 4 lines of 25
characters, Tom Thumb 4x6 fits 5 lines of 32 -- and the point of shipping
both was to be able to choose on the panel. Rows marked optional are dropped
first when the font only affords four lines.

Every value goes through `fmt()`, which is the single place a stale or
missing reading becomes "--". Scattering that check through the layout is
how a HUD ends up displaying one field's last-known number forever.
"""

import numpy as np

from oled_hud.pack import pack_bits
from oled_hud.hud.producers import format_uptime

WIDTH = 128
HEIGHT = 32
MISSING = "--"


def fmt(snap: dict, key: str, spec: str = "{:.1f}", *, now: float) -> str:
    """Format one reading, or MISSING if it is absent or aged out."""
    reading = snap.get(key)
    if reading is None or not reading.fresh(now):
        return MISSING
    return spec.format(reading.value)


def row(fields: list[str], cols: int) -> str:
    """Spread `fields` across `cols`: first flush left, last flush right,
    any middle ones evenly spaced between.

    Packing several fields per line is what lets the 4-line font carry the
    same content as the 5-line one. A two-field row on a 25-column panel
    leaves a dozen dead columns in the middle of every line; three fields
    use that space instead of buying a fifth line of smaller type.

    If the fields can't all fit, the first is truncated: the later ones are
    measurements (a temperature, a percentage) and the first is the label
    that introduces them, and a clipped label still reads.
    """
    fields = [f for f in fields if f]
    if not fields:
        return ""
    if len(fields) == 1:
        return fields[0][:cols]

    gaps = len(fields) - 1
    space = cols - sum(len(f) for f in fields)
    if space < gaps:
        fields = [fields[0][: max(0, len(fields[0]) + space - gaps)]] + fields[1:]
        space = cols - sum(len(f) for f in fields)
    base, extra = divmod(space, gaps)

    out = fields[0]
    for i, field in enumerate(fields[1:]):
        out += " " * (base + (1 if i < extra else 0)) + field
    # Truncating the first field can still leave an over-long line if a
    # single measurement is wider than the panel; clamp rather than let it
    # run off the edge and rely on the font's clipping to hide the bug.
    return out[:cols]


class SysView:
    """Host, CPU, memory, disk and network from the local-system producers."""

    name = "sys"

    def __init__(self, font):
        self.font = font
        self.lines = HEIGHT // font.height
        self.cols = WIDTH // font.advance
        # Center the leftover pixels when the lines don't divide 32 evenly
        # (Tom Thumb: 5 rows of 6 leaves 2).
        self.y0 = (HEIGHT - self.lines * font.height) // 2

    def rows(self, snap: dict, now: float) -> list[tuple[list[str], bool]]:
        """(fields, optional) per display row.

        Grouped by what belongs together rather than one metric per line, so
        the whole set fits four readable lines instead of needing five small
        ones. Worst case ("cpu 100%", "100.0C", "ld 12.34") is 24 of 25
        columns, so nothing truncates in practice.
        """
        host = snap.get("sys.host")
        up = snap.get("sys.uptime")
        return [
            (
                [
                    host.value if host is not None and host.fresh(now) else MISSING,
                    f"up {format_uptime(up.value)}"
                    if up is not None and up.fresh(now)
                    else f"up {MISSING}",
                ],
                False,
            ),
            (
                [
                    "cpu " + fmt(snap, "cpu.usage", "{:.0f}%", now=now),
                    fmt(snap, "cpu.temp", "{:.1f}C", now=now),
                    "ld " + fmt(snap, "load.1", "{:.2f}", now=now),
                ],
                False,
            ),
            (
                [
                    "mem " + fmt(snap, "mem.used_mb", "{:.0f}", now=now)
                    + "/" + fmt(snap, "mem.total_mb", "{:.0f}M", now=now),
                    "dsk " + fmt(snap, "disk.pct", "{:.0f}%", now=now),
                ],
                False,
            ),
            (
                [
                    fmt(snap, "net.ip", "{}", now=now),
                    fmt(snap, "disk.free_gb", "{:.1f}G", now=now),
                ],
                False,
            ),
            # Only shown by a font that affords a fifth line: the 5- and
            # 15-minute load averages, which say whether the 1-minute figure
            # on row 2 is a spike or a trend.
            (
                [
                    "ld5 " + fmt(snap, "load.5", "{:.2f}", now=now),
                    "ld15 " + fmt(snap, "load.15", "{:.2f}", now=now),
                ],
                True,
            ),
        ]

    def select(self, rows: list[tuple[list[str], bool]]) -> list[list[str]]:
        """Drop optional rows, last one first, until the set fits the font."""
        kept = list(rows)
        while len(kept) > self.lines:
            drop = next((i for i in range(len(kept) - 1, -1, -1) if kept[i][1]), None)
            if drop is None:
                del kept[self.lines :]  # nothing optional left; truncate
                break
            del kept[drop]
        return [fields for fields, _ in kept]

    def render(self, canvas: np.ndarray, snap: dict, now: float) -> None:
        """Draw into a (32, 128) bool canvas. Caller clears it."""
        for i, fields in enumerate(self.select(self.rows(snap, now))):
            self.font.draw(canvas, row(fields, self.cols), 0, self.y0 + i * self.font.height)

    def render_into(self, fb: np.ndarray, snap: dict, now: float) -> None:
        """Render and pack straight into a Compositor framebuffer.

        The bool canvas is the intermediate because text rows land at
        arbitrary pixel offsets, not page boundaries; `pack_bits` converts it
        to the page-major layout `Compositor.fb` and `blit()` share.
        """
        canvas = np.zeros((HEIGHT, WIDTH), dtype=bool)
        self.render(canvas, snap, now)
        fb[...] = pack_bits(canvas)
