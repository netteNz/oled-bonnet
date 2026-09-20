"""HUD playground -- live BTC-USD price with a trend sparkline over a
selectable window, through the Compositor (H0), no Store/producers/scheduler
scaffolding.

The sparkline is the main feature here, not the price digits -- it gets the
left 78px of the panel, the right 48px is a stacked readout of what the
graph is showing:

    +------------------------------------------+--------+
    |                                          |    15m |
    |   sparkline (78px)                       | 97,214 |
    |                                          | +1.24% |
    +------------------------------------------+--------+

Three right-aligned rows rather than two, because a combined
"15m  +1.24%" row needs 51px at its widest and the column is 48 -- and
widening the column to fit it would cost the graph 6px, which is the one
thing on this panel worth protecting. Stacking instead keeps the graph at
78px and leaves the price its own full-width row at size 12.

--window picks the span the line covers:

    24h   96 x 15-minute candles      1h    60 x 1-minute candles
    30m   30 x 1-minute candles       live  sliding, default 15m

None of these can be built from live WS ticks alone -- the panel hasn't
been running for the last 24h (or even the last 15 minutes), so there'd be
nothing to plot until it had been. Every window is seeded from the REST
`get_candles` endpoint so it's populated the moment the panel lights up.
What differs is how it moves afterwards:

  * 24h/1h/30m refetch the whole window every --refresh-interval. Between
    refetches the backdrop is fixed and only the tip moves, which is fine
    at these spans -- a 24h graph doesn't visibly change in 15 minutes.

  * live is a genuinely sliding window. Points carry their timestamps and
    are positioned on the x-axis by real time, not by index: WS ticks are
    appended every --live-sample seconds and anything older than
    --live-span falls off the left edge. So it starts as 15 one-minute
    candles, densifies with live ticks from the right, and after one full
    span is nothing but live ticks -- scrolling continuously the whole
    time instead of jumping on a refresh.

In every window the latest WS price is appended as one extra point at
render time (never stored) so the rightmost tip tracks the market in real
time between samples.

The sparkline auto-scales to whatever's in the current window each render
(min/max of the plotted points), not to an absolute price range -- that's
what makes the move visible instead of flatlining against a $100k+ BTC
price. That scaling is floored at MIN_RANGE_FRACTION of the price so a
quiet minute doesn't get amplified into a fake crash. A dotted rule marks
where the window opened, so "up or down since then" is readable without
doing arithmetic on the % row.

Auth goes through the official `coinbase-advanced-py` SDK's RESTClient and
WSClient, which build and sign the CDP JWT themselves and auto-detect the
CDP_API_SECRET's key type (Ed25519 base64 or EC PEM).

Credentials come from .env.secrets (gitignored, chmod 600) as CDP_API_KEY /
CDP_API_SECRET, never from argv or a constant in this file -- see
.env.secrets.example for the format and README.md's Setup section for why
the file isn't named plain `.env`.

Run with:
    .env/bin/python3 -m oled_hud.demos.btc_sparkline
    .env/bin/python3 -m oled_hud.demos.btc_sparkline --window 1h
    .env/bin/python3 -m oled_hud.demos.btc_sparkline --window live
    .env/bin/python3 -m oled_hud.demos.btc_sparkline --window live --live-span 300
"""

import argparse
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import board
import busio
import digitalio
import numpy as np
from coinbase.rest import RESTClient
from coinbase.websocket import WSClient
from PIL import Image, ImageDraw, ImageFont

from oled_hud.clock import FrameClock
from oled_hud.driver import PartialSSD1305
from oled_hud.hud.compositor import Compositor
from oled_hud.pack import pack_bits

WIDTH = 128
HEIGHT = 32

PRODUCT_ID = "BTC-USD"


@dataclass(frozen=True)
class Window:
    """One --window choice. `refresh` None marks the sliding mode: instead
    of refetching candles on a timer, the window is seeded once and then
    carried forward by live ticks, with points placed on the x-axis by
    timestamp so it scrolls."""

    label: str
    seconds: int
    granularity: str
    refresh: float | None

    @property
    def is_live(self) -> bool:
        return self.refresh is None


# Candle counts for the fixed windows are chosen to land near the graph's
# 78px width -- more points than columns just overplots, fewer leaves the
# line interpolating between samples. All are far under the API's
# 350-candle cap. `live` seeds at the finest granularity Coinbase offers,
# since its seed only has to hold until live ticks replace it.
WINDOWS = {
    "24h": Window("24h", 24 * 3600, "FIFTEEN_MINUTE", 900.0),
    "1h": Window("1h", 3600, "ONE_MINUTE", 60.0),
    "30m": Window("30m", 1800, "ONE_MINUTE", 60.0),
    "live": Window("live", 15 * 60, "ONE_MINUTE", None),
}

# Right column is sized to the widest thing that has to fit in it: a
# 6-figure price at size 12 measures 45px. Everything right-aligns to the
# panel edge, and a 2px blank gutter keeps the graph off the digits.
PRICE_W = 48
GUTTER = 2
PRICE_X0 = WIDTH - PRICE_W
PRICE_X_RIGHT = WIDTH - 1
GRAPH_X0 = 0
GRAPH_X1 = PRICE_X0 - GUTTER - 1
GRAPH_W = GRAPH_X1 - GRAPH_X0 + 1

# Bands are (top, height) in panel rows; text is vertically centred in its
# band against a fixed digit bbox so the rows stay aligned even when the
# price font shrinks for a wider number.
LABEL_BAND = (0, 9)
PRICE_BAND = (9, 14)
DELTA_BAND = (23, 9)

# Size 12 fits a 6-figure price; the smaller sizes are the fallback ladder
# for a 7-figure one, so a $1M+ BTC degrades to smaller digits instead of
# running off the panel.
PRICE_FONT_SIZES = (12, 11, 10, 9)
LABEL_FONT_SIZE = 8
DELTA_FONT_SIZE = 8

# Reference glyphs for vertical centring -- using the real text would make
# a row's baseline jump depending on whether it happened to contain a
# comma or a minus sign.
_METRICS_REF = "0123456789,%+-hms"

GRAPH_PAD = 2
BASELINE_DASH = 3

# Floor on the plotted price range, as a fraction of the current price.
# Without it the auto-scale is pure amplification: over a quiet 15-minute
# window BTC often moves less than a tenth of a percent, and stretching
# that to 28px of panel turns $20 of noise into a chart that looks like a
# crash. At 0.1% a genuine move still fills the panel; nothing smaller
# gets to pretend it's one.
MIN_RANGE_FRACTION = 0.001

SECRETS_PATH = Path(__file__).resolve().parents[2] / ".env.secrets"


def load_secrets(path: Path = SECRETS_PATH) -> dict[str, str]:
    """Parse KEY=value lines from .env.secrets."""
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


class LiveState:
    """Latest price, written by the WS thread and read by the frame loop --
    a lock because those are two different threads, not just for show."""

    def __init__(self):
        self._lock = threading.Lock()
        self.price: float | None = None

    def set_price(self, price: float) -> None:
        with self._lock:
            self.price = price

    def get_price(self) -> float | None:
        with self._lock:
            return self.price


def handle_ws_message(state: LiveState, raw_message: str) -> None:
    """Parses the raw dict directly rather than the SDK's own
    WebsocketResponse wrapper: that wrapper does `data.pop("client_id")`
    unconditionally, but live ticker messages from the current API don't
    carry a client_id field, so it raises KeyError on every message."""
    try:
        data = json.loads(raw_message)
    except json.JSONDecodeError:
        return
    if data.get("channel") != "ticker":
        return
    for event in data.get("events", []):
        for ticker in event.get("tickers", []):
            if ticker.get("product_id") == PRODUCT_ID and ticker.get("price") is not None:
                state.set_price(float(ticker["price"]))


def fetch_history(client: RESTClient, window: Window, span: float) -> list[tuple[float, float]]:
    """(timestamp, close) across `span` seconds, oldest first -- the API
    returns newest-first, so this sorts by `start` rather than trusting
    response order. Timestamps come back because the sliding window places
    points by real time, not by index."""
    end = int(time.time())
    start = end - int(span)
    response = client.get_candles(
        product_id=PRODUCT_ID,
        start=str(start),
        end=str(end),
        granularity=window.granularity,
    )
    candles = sorted(response.candles or [], key=lambda c: int(c.start))
    return [(float(c.start), float(c.close)) for c in candles if c.close is not None]


def format_span(seconds: float) -> str:
    """Compact span label for the top row -- '15m', '1h', '24h'."""
    seconds = int(seconds)
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _format_price(price: float) -> str:
    """No "$" and no cents: the column is 48px and neither character earns
    its width next to a six-figure number."""
    return f"{price:,.0f}"


def _format_delta(pct: float) -> str:
    """Precision shrinks as the move grows -- two decimals matter on a
    0.4% day, they're noise on a 100% one, and the column is the same
    48px either way."""
    if abs(pct) >= 100:
        return f"{pct:+.0f}%"
    if abs(pct) >= 10:
        return f"{pct:+.1f}%"
    return f"{pct:+.2f}%"


def _fit_price_font(draw: ImageDraw.ImageDraw, text: str) -> ImageFont.ImageFont:
    """Largest size from the ladder whose rendering of `text` fits the
    column, so the price shrinks rather than overflowing."""
    limit = PRICE_W - 1
    for size in PRICE_FONT_SIZES:
        font = ImageFont.load_default(size=size)
        if draw.textlength(text, font=font) <= limit:
            return font
    return ImageFont.load_default(size=PRICE_FONT_SIZES[-1])


def _draw_right(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    band: tuple[int, int],
) -> None:
    """Right-align `text` to the panel edge, vertically centred in `band`."""
    top, height = band
    bbox = draw.textbbox((0, 0), _METRICS_REF, font=font)
    ink_h = bbox[3] - bbox[1]
    x = PRICE_X_RIGHT - draw.textlength(text, font=font)
    y = top + (height - ink_h) // 2 - bbox[1]
    draw.text((x, y), text, fill=255, font=font)


def x_positions(
    timestamps: Sequence[float], window: Window, span: float, now: float
) -> np.ndarray:
    """Graph x for each point.

    A sliding window maps real time onto the axis -- `now` sits at the
    right edge and `now - span` at the left -- so points drift leftward at
    a constant rate and age off the end. A fixed window spreads its points
    evenly instead, which keeps sample count and graph width independent:
    96, 60 and 30 candles all fill the same 78px.
    """
    n = len(timestamps)
    if window.is_live:
        ages = np.clip((now - np.asarray(timestamps, dtype=float)) / span, 0.0, 1.0)
        return GRAPH_X1 - ages * (GRAPH_X1 - GRAPH_X0)
    return np.linspace(GRAPH_X0, GRAPH_X1, n)


def _draw_sparkline(
    draw: ImageDraw.ImageDraw, xs: Sequence[float], values: Sequence[float]
) -> None:
    """Plot `values` at `xs`, auto-scaled to their own min/max, with a
    dotted rule at the window's opening level and a dot on the current
    tip."""
    if len(values) < 2:
        return

    plot_h = HEIGHT - 2 * GRAPH_PAD
    x_first = int(round(min(xs)))

    lo, hi = min(values), max(values)
    span = hi - lo
    floor = abs(values[-1]) * MIN_RANGE_FRACTION
    if span < floor:
        # Re-centre the window on the data rather than growing from `lo`,
        # so a flat stretch sits mid-panel instead of pinned to the bottom.
        mid = (hi + lo) / 2
        lo, hi = mid - floor / 2, mid + floor / 2
        span = hi - lo

    if span <= 0:
        y_mid = HEIGHT // 2
        draw.line([(xs[0], y_mid), (xs[-1], y_mid)], fill=255)
        return

    def y_for(value: float) -> float:
        return GRAPH_PAD + plot_h - (value - lo) / span * plot_h

    # Opening level first, so the data line draws over it where they meet
    # rather than the reference cutting through the data. It starts where
    # the data starts -- running it the full width would imply a window
    # that a part-filled one doesn't have yet.
    base_y = int(round(y_for(values[0])))
    for x in range(x_first, GRAPH_X1 + 1, BASELINE_DASH):
        draw.point((x, base_y), fill=255)

    points = [(x, y_for(v)) for x, v in zip(xs, values)]
    draw.line(points, fill=255, width=1)

    # 2x2 tip marker -- a 1px line end is easy to lose against the dotted
    # rule, and "where are we now" is the whole point of the graph.
    tip_x, tip_y = int(round(points[-1][0])), int(round(points[-1][1]))
    draw.rectangle(
        [max(GRAPH_X0, tip_x - 1), max(0, tip_y - 1), tip_x, min(HEIGHT - 1, tip_y)],
        fill=255,
    )


def render_into(
    comp: Compositor,
    price: float,
    series: Sequence[tuple[float, float]],
    window: Window,
    span: float,
    now: float | None = None,
) -> None:
    """All PIL/font work happens here, off the frame path -- called only
    when the price, the series or the scroll position actually changed,
    not every frame.

    `series` is the window's (timestamp, price) backdrop; the live `price`
    is appended as one more plotted point at `now` so the line's rightmost
    tip tracks the current price in real time, without that live tick ever
    being written back into the stored series.
    """
    now = time.time() if now is None else now
    img = Image.new("1", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)

    points = [*series, (now, price)]
    values = [v for _, v in points]

    _draw_right(draw, format_span(span), ImageFont.load_default(size=LABEL_FONT_SIZE), LABEL_BAND)

    price_text = _format_price(price)
    _draw_right(draw, price_text, _fit_price_font(draw, price_text), PRICE_BAND)

    # "--" while there's no opening price to measure against yet; a 0.00%
    # that isn't true reads worse than an obvious placeholder.
    opening = values[0]
    delta_text = _format_delta((price - opening) / opening * 100) if len(values) > 1 and opening else "--"
    _draw_right(draw, delta_text, ImageFont.load_default(size=DELTA_FONT_SIZE), DELTA_BAND)

    _draw_sparkline(draw, x_positions([t for t, _ in points], window, span, now), values)

    bits = pack_bits(np.asarray(img, dtype=np.uint8) > 0)
    comp.fb[...] = bits


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--window", choices=list(WINDOWS), default="24h",
        help="span the sparkline covers; 'live' slides continuously on WS ticks",
    )
    parser.add_argument("--fps", type=float, default=10.0, help="frame loop rate")
    parser.add_argument(
        "--refresh-interval", type=float, default=None,
        help="seconds between candle-history refreshes (default: per --window); "
             "ignored for --window live, which slides instead of refetching",
    )
    parser.add_argument(
        "--live-span", type=float, default=None,
        help="seconds covered by --window live (default 900, i.e. 15m)",
    )
    parser.add_argument(
        "--live-sample", type=float, default=5.0,
        help="seconds between stored WS samples for --window live; the tip always "
             "shows the latest price regardless, so this only sets line density",
    )
    parser.add_argument(
        "--seconds", type=float, default=0.0, help="run time; 0 runs until Ctrl+C"
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    window = WINDOWS[args.window]
    span = float(args.live_span) if (window.is_live and args.live_span) else float(window.seconds)
    refresh_interval = args.refresh_interval if args.refresh_interval is not None else window.refresh

    creds = load_secrets()
    client = RESTClient(api_key=creds["CDP_API_KEY"], api_secret=creds["CDP_API_SECRET"])
    state = LiveState()

    # (timestamp, price), oldest first. The sliding window pops from the
    # left as points age out, which is the one operation a list is bad at.
    series: deque[tuple[float, float]] = deque()

    # Seed the price and the backdrop up front so the panel isn't blank --
    # and, for the sliding window, so it's a full 15 minutes of trend from
    # the first frame rather than a line that takes 15 minutes to grow in.
    try:
        state.set_price(float(client.get_product(PRODUCT_ID).price))
    except Exception as exc:
        print(f"initial price fetch failed: {exc}")
    try:
        series.extend(fetch_history(client, window, span))
    except Exception as exc:
        print(f"initial {format_span(span)} history fetch failed: {exc}")

    ws_client = WSClient(
        api_key=creds["CDP_API_KEY"],
        api_secret=creds["CDP_API_SECRET"],
        on_message=lambda raw: handle_ws_message(state, raw),
    )
    ws_client.open()
    ws_client.ticker([PRODUCT_ID])

    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    display = PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)
    display.fill(0)
    display.show()

    comp = Compositor(display)

    clock = FrameClock(args.fps)
    next_refresh = refresh_interval or 0.0
    next_sample = args.live_sample
    # A sliding window has to redraw as time passes even when the price is
    # flat, but only when the scroll has actually moved a whole pixel --
    # redrawing faster than that is PIL work for an identical frame.
    seconds_per_pixel = span / GRAPH_W
    revision = 0
    last_rendered = None
    history_refreshes, history_refresh_errors, live_samples, aged_out = 0, 0, 0, 0
    try:
        while not args.seconds or clock.elapsed < args.seconds:
            now = time.time()
            if window.is_live:
                if clock.elapsed >= next_sample:
                    sampled = state.get_price()
                    if sampled is not None:
                        series.append((now, sampled))
                        live_samples += 1
                        revision += 1
                    next_sample = clock.elapsed + args.live_sample
                cutoff = now - span
                while series and series[0][0] < cutoff:
                    series.popleft()
                    aged_out += 1
                    revision += 1
            elif clock.elapsed >= next_refresh:
                try:
                    refreshed = fetch_history(client, window, span)
                    series.clear()
                    series.extend(refreshed)
                    history_refreshes += 1
                    revision += 1
                except Exception as exc:
                    history_refresh_errors += 1
                    print(f"{format_span(span)} history refresh failed, keeping last window: {exc}")
                next_refresh = clock.elapsed + refresh_interval

            current_price = state.get_price()
            scroll = int(now / seconds_per_pixel) if window.is_live else 0
            current = (current_price, revision, scroll)
            if current != last_rendered and current_price is not None:
                render_into(comp, current_price, series, window, span, now)
                last_rendered = current

            comp.flush()
            clock.tick()
    except KeyboardInterrupt:
        pass
    finally:
        ws_client.close()
        display.fill(0)
        display.show()
        print(clock.summary())
        if window.is_live:
            print(f"live samples: {live_samples} plotted, {aged_out} aged out, {len(series)} in window")
        else:
            print(f"history refreshes: {history_refreshes} ok, {history_refresh_errors} failed")


if __name__ == "__main__":
    main()
