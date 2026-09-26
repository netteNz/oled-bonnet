"""Coinbase portfolio producer: every holding in the default portfolio, with
its USD value, written into the Store for `views.PortfolioView`.

One `get_portfolio_breakdown()` call returns every spot position already
priced in fiat, so this producer never has to know which assets you hold or
look up a price per asset -- unlike `demos/coinbase_ticker.py`, whose
HOLDINGS list has to be edited by hand every time you buy something new.
The portfolio UUID is looked up once and cached; it doesn't change.

Polled, not streamed: the view is one slot in a rotation and a balance
doesn't move tick-to-tick, so a WebSocket would buy nothing here but a second
connection to keep alive. A failed poll raises into `ProducerThread`'s error
path like any other producer, so the last good numbers age out to "--" via
the TTL instead of freezing on screen.

The `coinbase-advanced-py` SDK is imported lazily inside `CoinbasePortfolio`,
so importing this module -- and running the daemon without the portfolio
view -- never needs the SDK or credentials. Parsing is a pure function over
the response dict (design rule 4), testable without either.

Credentials come from .env.secrets (CDP_API_KEY / CDP_API_SECRET), the same
file and names the Coinbase demos use.
"""

from pathlib import Path

from oled_hud.hud.producers import Producer

SECRETS_PATH = Path(__file__).resolve().parents[2] / ".env.secrets"

# Positions worth less than this are dropped: exchange accounts accumulate
# sub-cent dust from fees and airdrops, and a row reading "$0.00" is a row a
# real holding didn't get.
DUST_USD = 0.01


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


def _as_dict(response) -> dict:
    """SDK responses are typed objects in some versions and dicts in others
    (the ticker demo trips over exactly this with `available_balance`);
    normalize once here so the parser only ever sees plain dicts."""
    if isinstance(response, dict):
        return response
    if hasattr(response, "to_dict"):
        return response.to_dict()
    return dict(response)


def _money(field) -> float:
    """Fiat fields arrive either as a bare number or as {"value", "currency"}."""
    if isinstance(field, dict):
        field = field.get("value", 0)
    return float(field or 0)


def parse_breakdown(data: dict) -> dict[str, object]:
    """get_portfolio_breakdown() response -> Store values.

    `pf.positions` is a tuple of (asset, quantity, usd) sorted by USD value,
    largest first, dust removed -- a tuple rather than per-asset keys because
    the set of assets changes when you trade, and per-asset keys for a sold
    holding would linger in the Store until their TTL ran out.
    """
    breakdown = data.get("breakdown", data)
    positions = []
    for pos in breakdown.get("spot_positions") or []:
        usd = _money(pos.get("total_balance_fiat"))
        if usd < DUST_USD:
            continue
        positions.append((str(pos.get("asset", "?")), _money(pos.get("total_balance_crypto")), usd))
    positions.sort(key=lambda p: p[2], reverse=True)

    total = (breakdown.get("portfolio_balances") or {}).get("total_balance")
    return {
        "pf.total": _money(total) if total is not None else sum(p[2] for p in positions),
        "pf.positions": tuple(positions),
    }


class CoinbasePortfolio(Producer):
    name = "portfolio"
    interval = 60.0

    def __init__(self, secrets_path: Path = SECRETS_PATH, client=None):
        self._secrets_path = secrets_path
        self._client = client
        self._uuid: str | None = None

    def _get_client(self):
        if self._client is None:
            from coinbase.rest import RESTClient

            creds = load_secrets(self._secrets_path)
            self._client = RESTClient(api_key=creds["CDP_API_KEY"],
                                      api_secret=creds["CDP_API_SECRET"])
        return self._client

    def _portfolio_uuid(self, client) -> str:
        if self._uuid is None:
            portfolios = _as_dict(client.get_portfolios()).get("portfolios") or []
            if not portfolios:
                raise RuntimeError("no portfolios visible to this API key")
            default = next((p for p in portfolios if p.get("type") == "DEFAULT"), portfolios[0])
            self._uuid = default["uuid"]
        return self._uuid

    def warm(self) -> None:
        """Import the SDK and resolve the portfolio UUID before the frame
        loop runs. Measured on rasp3: doing this on the producer thread's
        first poll (~1.3s) made 3 of 1200 frames late; a sys-only run
        had 0."""
        self._portfolio_uuid(self._get_client())

    def poll(self) -> dict[str, object]:
        client = self._get_client()
        return parse_breakdown(_as_dict(client.get_portfolio_breakdown(self._portfolio_uuid(client))))
