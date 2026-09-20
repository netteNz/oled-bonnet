"""HUD views (Phase H1 local-system view; Phase H2 the rest).

A view turns a `Store` snapshot into pixels and nothing else: it owns no
timer, no polling and no panel. That keeps the H2 scheduler free to decide
*when* a view is drawn without the view having an opinion, and keeps this
module testable against a dict with no hardware anywhere. `View.refresh` is
the one exception -- a hook for a view whose pixels depend on wall-clock time
rather than any `Reading`, so it still has no polling and no panel, just a
cadence the scheduler honors alongside `store.version`.

Layout is computed from the font rather than hardcoded, because the two
vendored fonts give different budgets -- Spleen 5x8 fits 4 lines of 25
characters, Tom Thumb 4x6 fits 5 lines of 32 -- and the point of shipping
both was to be able to choose on the panel. Rows marked optional are dropped
first when the font only affords four lines.

Every value goes through `fmt()`, which is the single place a stale or
missing reading becomes "--" -- including the ones that need a conversion
first, which is what `fmt`'s `transform` hook is for. Scattering that check
through the layout is how a HUD ends up displaying one field's last-known
number forever.
"""

import time
from collections.abc import Callable

import numpy as np

from oled_hud.hud import HEIGHT, WIDTH
from oled_hud.hud.font import Font, upscale
from oled_hud.hud.producers import format_uptime
from oled_hud.hud.store import Snapshot
from oled_hud.pack import pack_bits

MISSING = "--"

_WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def fmt(snap: Snapshot, key: str, spec: str = "{:.1f}", *, now: float,
        transform: Callable[[object], object] | None = None) -> str:
    """Format one reading, or MISSING if it is absent or aged out.

    `transform` runs between the reading and the spec, for values that need
    a conversion rather than a format -- uptime seconds into "5h18m". It is
    deliberately applied *after* the freshness check, so a stale reading
    costs nothing and can never reach a transform expecting a live value.
    """
    reading = snap.get(key)
    if reading is None or not reading.fresh(now):
        return MISSING
    value = transform(reading.value) if transform is not None else reading.value
    return spec.format(value)


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


class View:
    """What the H2 scheduler needs of anything it can put on the panel.

    A base class rather than a `Protocol` (contrast `compositor.Driver`,
    which is a Protocol on purpose): there, the tests deliberately pass a
    recorder instead of real hardware and there is no shared code to give
    them. Here every view's `render_into()` is the same three lines --
    zero the canvas, call the subclass's `render()`, pack it -- so making
    that concrete and inherited is a real saving, not an accident of taste.
    """

    #: Identifies this view to the scheduler, which selects among several
    #: by name.
    name = ""

    #: Seconds between forced re-renders even if `store.version` hasn't
    #: changed. 0 means "only a data change or a scheduler event should
    #: redraw this view" -- the H1 behavior, kept as the default so a plain
    #: `SysView` costs the scheduler nothing extra. A view whose pixels
    #: depend on wall-clock time rather than on any `Reading` (a clock)
    #: sets this instead of pretending to be a producer.
    refresh: float = 0.0

    def render(self, canvas: np.ndarray, snap: Snapshot, now: float) -> None:
        raise NotImplementedError

    def render_into(self, fb: np.ndarray, snap: Snapshot, now: float) -> None:
        """Render and pack straight into a Compositor framebuffer.

        The bool canvas is the intermediate because content lands at
        arbitrary pixel offsets, not page boundaries; `pack_bits` converts it
        to the page-major layout `Compositor.fb` and `blit()` share.
        """
        canvas = np.zeros((HEIGHT, WIDTH), dtype=bool)
        self.render(canvas, snap, now)
        fb[...] = pack_bits(canvas)


class SysView(View):
    """Host, CPU, memory, disk and network from the local-system producers."""

    name = "sys"

    def __init__(self, font: Font):
        self.font = font
        self.lines = HEIGHT // font.height
        self.cols = WIDTH // font.advance
        # Center the leftover pixels when the lines don't divide 32 evenly
        # (Tom Thumb: 5 rows of 6 leaves 2).
        self.y0 = (HEIGHT - self.lines * font.height) // 2

    def rows(self, snap: Snapshot, now: float) -> list[tuple[list[str], bool]]:
        """(fields, optional) per display row.

        Grouped by what belongs together rather than one metric per line, so
        the whole set fits four readable lines instead of needing five small
        ones. Worst case ("cpu 100%", "100.0C", "ld 12.34") is 24 of 25
        columns, so nothing truncates in practice.
        """
        return [
            (
                [
                    fmt(snap, "sys.host", "{}", now=now),
                    "up " + fmt(snap, "sys.uptime", "{}", now=now,
                                transform=format_uptime),
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
                    "mem " + fmt(snap, "mem.used_mb", "{:.0f}", now=now) + "/"
                    + fmt(snap, "mem.total_mb", "{:.0f}M", now=now),
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

    def render(self, canvas: np.ndarray, snap: Snapshot, now: float) -> None:
        """Draw into a (32, 128) bool canvas. Caller clears it."""
        for i, fields in enumerate(self.select(self.rows(snap, now))):
            self.font.draw(canvas, row(fields, self.cols), 0, self.y0 + i * self.font.height)


class ClockView(View):
    """Big-type wall clock, for a HUD scheduler rotation.

    Wall time, not the monotonic clock everything else in this package is
    built on: `Store`, `ProducerThread` and `FrameClock` all use
    `time.monotonic`/`perf_counter` because a `Reading`'s age must never jump
    backwards under an NTP step. A clock has the opposite job -- show what a
    clock on the desk would show -- so it reads `wall`/`localtime` directly
    rather than going through a `Reading`, and ignores the `now` argument
    `render()` is handed (that `now` is `store.now()`, the monotonic one).
    Both hooks are injected so a test can pin the rendered string without
    depending on the test machine's timezone (pass `localtime=time.gmtime`)
    or the wall clock actually moving.

    `refresh = 1.0` asks the scheduler to redraw this view once a second, but
    "HH:MM" only changes once a minute -- so 59 of every 60 renders produce a
    canvas byte-identical to the one already on screen, and the Compositor's
    diff turns those into zero pushes. That's why the gutter next to the big
    digits carries the day of week rather than seconds: a seconds readout
    would make every one of those 60 renders genuinely different and turn a
    near-free view into one that pushes every second forever.
    """

    name = "clock"
    refresh = 1.0

    def __init__(self, font: Font, *, scale: int = 4,
                 wall: Callable[[], float] = time.time,
                 localtime: Callable[[float], time.struct_time] = time.localtime):
        self.font = font
        self.scale = scale
        self.wall = wall
        self.localtime = localtime
        self.big_h = font.height * scale
        self.y0 = (HEIGHT - self.big_h) // 2

    def render(self, canvas: np.ndarray, snap: Snapshot, now: float) -> None:
        t = self.localtime(self.wall())
        text = f"{t.tm_hour:02d}:{t.tm_min:02d}"
        big = upscale(self.font.render(text), self.scale)
        h, w = big.shape
        x0 = (WIDTH - w) // 2
        canvas[self.y0 : self.y0 + h, x0 : x0 + w] |= big

        # Whatever width is left of the centered clock carries one small
        # status field -- the day of week, which (like the hour and minute)
        # is stable for the whole minute, keeping the byte-identical-frame
        # property above intact.
        gutter = WIDTH - (x0 + w)
        if gutter >= self.font.advance * len(_WEEKDAYS[0]):
            day = _WEEKDAYS[t.tm_wday]
            gx = x0 + w + max(0, (gutter - self.font.measure(day)) // 2)
            self.font.draw(canvas, day, gx, self.y0)


class AlertView(View):
    """Firing-alert banner, shown by the scheduler while a threshold holds.

    Takes the alert to display through `show()` rather than through
    `render()`'s signature, so it still satisfies the plain `View` contract
    everything else in the rotation is treated through -- the scheduler is
    the only thing in H2 that knows `alerts.py` exists; this view just draws
    whatever it's handed and shows nothing at all if it's handed nothing.

    The rule's own value goes through `fmt()`, exactly like `SysView`'s
    rows, so a reading that goes stale *while its own alert is on screen*
    still degrades to "--" instead of the last number that triggered it --
    the alert's existence should never make a number look more current than
    the freshness discipline everywhere else in this module allows.
    """

    name = "alert"
    refresh = 1.0  # keeps the elapsed-time readout ticking once a second

    def __init__(self, font: Font, label_font: Font | None = None):
        self.font = font
        self.label_font = label_font or font
        self._alert = None

    def show(self, alert) -> None:
        """Set the alert to display. `alert=None` blanks the view."""
        self._alert = alert

    def render(self, canvas: np.ndarray, snap: Snapshot, now: float) -> None:
        if self._alert is None:
            return
        rule = self._alert.rule

        label = upscale(self.label_font.render(rule.label), 2)
        lh, lw = label.shape
        lx = max(0, (WIDTH - lw) // 2)
        visible = min(lw, WIDTH - lx)
        canvas[:lh, lx : lx + visible] |= label[:, :visible]

        value = fmt(snap, rule.key, rule.spec, now=now)
        age = format_uptime(max(0.0, now - self._alert.since))
        line = f"{rule.key} {value}  {age}"
        y = lh + 1
        if y + self.font.height <= HEIGHT:
            self.font.draw(canvas, line, 2, y)
