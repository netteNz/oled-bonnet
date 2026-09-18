"""HUD playground -- live BTC-USD price and your BTC holding, through the
Compositor (H0), no Store/producers/scheduler scaffolding.

Price comes from a WebSocket `ticker` subscription (WSClient), pushed the
instant it changes -- REST polling always has a visible lag between the
market moving and the panel catching up. Holdings come from a slower REST
poll (--poll-interval) instead: a balance doesn't move tick-to-tick the way
a price does, so there's nothing to gain from streaming it, and it's one
fewer authenticated call fighting for the WS connection's attention. Same
split the user's other Coinbase project uses between its ticker/user
channels and its REST reconciliation.

The frame loop only re-renders when price or balance actually changed since
the last frame -- render_into() does real PIL work, and most frames between
ticks have nothing new to draw.

Auth goes through the official `coinbase-advanced-py` SDK's RESTClient and
WSClient, which build and sign the CDP JWT themselves and auto-detect the
CDP_API_SECRET's key type (Ed25519 base64 or EC PEM).

Credentials come from .env.secrets (gitignored, chmod 600) as CDP_API_KEY /
CDP_API_SECRET, never from argv or a constant in this file -- see
.env.secrets.example for the format and README.md's Setup section for why
the file isn't named plain `.env`.

Run with:
    .env/bin/python3 -m oled_hud.demos.coinbase_ticker
    .env/bin/python3 -m oled_hud.demos.coinbase_ticker --poll-interval 30 --seconds 120
"""

import argparse
import json
import threading
from pathlib import Path

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
HOLDING_CURRENCY = "BTC"

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
    """Latest price (written by the WS thread) and balance (written by the
    frame-loop thread's REST poll), read by the frame loop -- a lock because
    those are two different threads, not just for show."""

    def __init__(self):
        self._lock = threading.Lock()
        self.price: float | None = None
        self.balance: float | None = None

    def set_price(self, price: float) -> None:
        with self._lock:
            self.price = price

    def set_balance(self, balance: float) -> None:
        with self._lock:
            self.balance = balance

    def snapshot(self) -> tuple[float | None, float | None]:
        with self._lock:
            return self.price, self.balance


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


def fetch_btc_balance(client: RESTClient) -> float:
    """Sum the BTC account(s), paginating get_accounts() to exhaustion --
    a single unpaginated page can silently miss balances on later pages."""
    balance = 0.0
    cursor = None
    while True:
        response = client.get_accounts(limit=250, cursor=cursor)
        for account in response.accounts or []:
            # available_balance comes back as a plain {"value", "currency"}
            # dict, not a nested typed object -- unlike Account itself.
            if account.currency == HOLDING_CURRENCY and account.available_balance:
                balance += float(account.available_balance["value"])
        if not getattr(response, "has_next", False):
            break
        cursor = getattr(response, "cursor", None)
        if not cursor:
            break
    return balance


def render_into(comp: Compositor, price: float, balance: float) -> None:
    """All PIL/font work happens here, off the frame path -- called only
    when price or balance actually changed, not every frame."""
    font = ImageFont.load_default()
    img = Image.new("1", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    draw.text((2, 2), f"BTC ${price:,.2f}", fill=255, font=font)
    draw.text((2, 17), f"{balance:.6f} = ${balance * price:,.2f}", fill=255, font=font)
    bits = pack_bits(np.asarray(img, dtype=np.uint8) > 0)
    comp.fb[...] = bits


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fps", type=float, default=10.0, help="frame loop rate")
    parser.add_argument(
        "--poll-interval", type=float, default=30.0,
        help="seconds between BTC balance refreshes (price is WS-pushed, not polled)",
    )
    parser.add_argument(
        "--seconds", type=float, default=0.0, help="run time; 0 runs until Ctrl+C"
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    creds = load_secrets()
    client = RESTClient(api_key=creds["CDP_API_KEY"], api_secret=creds["CDP_API_SECRET"])
    state = LiveState()

    # Seed both values up front so the panel isn't blank before the first
    # WS tick / REST poll lands.
    try:
        state.set_price(float(client.get_product(PRODUCT_ID).price))
    except Exception as exc:
        print(f"initial price fetch failed: {exc}")
    try:
        state.set_balance(fetch_btc_balance(client))
    except Exception as exc:
        print(f"initial balance fetch failed: {exc}")

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
    next_poll = 0.0
    last_rendered = (None, None)
    balance_polls, balance_poll_errors = 0, 0
    try:
        while not args.seconds or clock.elapsed < args.seconds:
            if clock.elapsed >= next_poll:
                try:
                    state.set_balance(fetch_btc_balance(client))
                    balance_polls += 1
                except Exception as exc:
                    balance_poll_errors += 1
                    print(f"balance poll failed, keeping last reading: {exc}")
                next_poll = clock.elapsed + args.poll_interval

            current = state.snapshot()
            if current != last_rendered and current[0] is not None:
                render_into(comp, current[0], current[1] or 0.0)
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
        print(f"balance polls: {balance_polls} ok, {balance_poll_errors} failed")


if __name__ == "__main__":
    main()
