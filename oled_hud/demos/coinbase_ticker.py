"""HUD playground -- live price and holding value for each asset in
HOLDINGS, through the Compositor (H0), no Store/producers/scheduler
scaffolding.

Price comes from a WebSocket `ticker` subscription (WSClient, one
subscription covering every product in HOLDINGS), pushed the instant it
changes -- REST polling always has a visible lag between the market moving
and the panel catching up. Holdings come from a slower REST poll
(--poll-interval) instead: a balance doesn't move tick-to-tick the way a
price does, so there's nothing to gain from streaming it, and it's one
fewer authenticated call fighting for the WS connection's attention. Same
split the user's other Coinbase project uses between its ticker/user
channels and its REST reconciliation.

Each holding gets one row (`HEIGHT // len(HOLDINGS)` px tall) -- the panel
is only 32px, so this is what fits BTC + SOL at the default font's ~13px
line height without overlap; a third holding would need a smaller font.

The frame loop only re-renders when any price or balance actually changed
since the last frame -- render_into() does real PIL work, and most frames
between ticks have nothing new to draw.

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

# One row per entry, top to bottom -- see the module docstring for why two
# is the practical limit at the default font.
HOLDINGS = [
    {"symbol": "BTC", "product_id": "BTC-USD", "currency": "BTC"},
    {"symbol": "SOL", "product_id": "SOL-USD", "currency": "SOL"},
]
PRODUCT_IDS = [h["product_id"] for h in HOLDINGS]

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
    """Latest price per product_id (written by the WS thread) and latest
    balance per currency (written by the frame-loop thread's REST poll),
    read by the frame loop -- a lock because those are two different
    threads, not just for show."""

    def __init__(self):
        self._lock = threading.Lock()
        self.prices: dict[str, float] = {}
        self.balances: dict[str, float] = {}

    def set_price(self, product_id: str, price: float) -> None:
        with self._lock:
            self.prices[product_id] = price

    def set_balance(self, currency: str, balance: float) -> None:
        with self._lock:
            self.balances[currency] = balance

    def snapshot(self) -> tuple[dict[str, float], dict[str, float]]:
        with self._lock:
            return dict(self.prices), dict(self.balances)


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
            product_id = ticker.get("product_id")
            if product_id in PRODUCT_IDS and ticker.get("price") is not None:
                state.set_price(product_id, float(ticker["price"]))


def fetch_balance(client: RESTClient, currency: str) -> float:
    """Sum the account(s) for one currency, paginating get_accounts() to
    exhaustion -- a single unpaginated page can silently miss balances on
    later pages."""
    balance = 0.0
    cursor = None
    while True:
        response = client.get_accounts(limit=250, cursor=cursor)
        for account in response.accounts or []:
            # available_balance comes back as a plain {"value", "currency"}
            # dict, not a nested typed object -- unlike Account itself.
            if account.currency == currency and account.available_balance:
                balance += float(account.available_balance["value"])
        if not getattr(response, "has_next", False):
            break
        cursor = getattr(response, "cursor", None)
        if not cursor:
            break
    return balance


def render_into(comp: Compositor, prices: dict[str, float], balances: dict[str, float]) -> None:
    """All PIL/font work happens here, off the frame path -- called only
    when any price or balance actually changed, not every frame."""
    font = ImageFont.load_default()
    img = Image.new("1", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    row_h = HEIGHT // len(HOLDINGS)
    for i, holding in enumerate(HOLDINGS):
        price = prices.get(holding["product_id"])
        balance = balances.get(holding["currency"], 0.0)
        if price is None:
            continue
        text = f"{holding['symbol']} {balance:.4f} = ${balance * price:,.2f}"
        draw.text((2, i * row_h + 2), text, fill=255, font=font)
    bits = pack_bits(np.asarray(img, dtype=np.uint8) > 0)
    comp.fb[...] = bits


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fps", type=float, default=10.0, help="frame loop rate")
    parser.add_argument(
        "--poll-interval", type=float, default=30.0,
        help="seconds between balance refreshes (prices are WS-pushed, not polled)",
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

    # Seed every price/balance up front so the panel isn't blank before the
    # first WS tick / REST poll lands.
    for holding in HOLDINGS:
        try:
            state.set_price(holding["product_id"], float(client.get_product(holding["product_id"]).price))
        except Exception as exc:
            print(f"initial price fetch failed for {holding['product_id']}: {exc}")
        try:
            state.set_balance(holding["currency"], fetch_balance(client, holding["currency"]))
        except Exception as exc:
            print(f"initial balance fetch failed for {holding['currency']}: {exc}")

    ws_client = WSClient(
        api_key=creds["CDP_API_KEY"],
        api_secret=creds["CDP_API_SECRET"],
        on_message=lambda raw: handle_ws_message(state, raw),
    )
    ws_client.open()
    ws_client.ticker(PRODUCT_IDS)

    i2c = busio.I2C(board.SCL, board.SDA)
    reset_pin = digitalio.DigitalInOut(board.D4)
    display = PartialSSD1305(WIDTH, HEIGHT, i2c, reset=reset_pin)
    display.fill(0)
    display.show()

    comp = Compositor(display)

    clock = FrameClock(args.fps)
    next_poll = 0.0
    last_rendered = ({}, {})
    balance_polls, balance_poll_errors = 0, 0
    try:
        while not args.seconds or clock.elapsed < args.seconds:
            if clock.elapsed >= next_poll:
                for holding in HOLDINGS:
                    try:
                        state.set_balance(holding["currency"], fetch_balance(client, holding["currency"]))
                        balance_polls += 1
                    except Exception as exc:
                        balance_poll_errors += 1
                        print(f"balance poll failed for {holding['currency']}, keeping last reading: {exc}")
                next_poll = clock.elapsed + args.poll_interval

            current = state.snapshot()
            if current != last_rendered and current[0]:
                render_into(comp, *current)
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
