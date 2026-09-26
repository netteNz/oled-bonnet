"""CoinbasePortfolio producer and PortfolioView, with no SDK or network."""

import numpy as np

from oled_hud.hud.font import load
from oled_hud.hud.portfolio import CoinbasePortfolio, parse_breakdown
from oled_hud.hud.store import Reading
from oled_hud.hud.views import PortfolioView, format_usd

NOW = 1000.0

BREAKDOWN = {
    "breakdown": {
        "portfolio_balances": {"total_balance": {"value": "5432.10", "currency": "USD"}},
        "spot_positions": [
            {"asset": "SOL", "total_balance_crypto": 12.5, "total_balance_fiat": 1800.0},
            {"asset": "BTC", "total_balance_crypto": 0.031, "total_balance_fiat": 3500.0},
            {"asset": "USD", "total_balance_crypto": 132.1, "total_balance_fiat": 132.1},
            {"asset": "SHIB", "total_balance_crypto": 3.0, "total_balance_fiat": 0.0001},
        ],
    }
}


def snap(**values):
    return {k: Reading(k, v, NOW, 60.0) for k, v in values.items()}


def test_parse_sorts_by_value_and_drops_dust():
    out = parse_breakdown(BREAKDOWN)
    assert out["pf.total"] == 5432.10
    assert [p[0] for p in out["pf.positions"]] == ["BTC", "SOL", "USD"]
    assert out["pf.positions"][0] == ("BTC", 0.031, 3500.0)


def test_parse_sums_positions_when_total_is_missing():
    data = {"breakdown": {"spot_positions": BREAKDOWN["breakdown"]["spot_positions"][:2]}}
    assert parse_breakdown(data)["pf.total"] == 5300.0


class FakeClient:
    def __init__(self):
        self.breakdown_calls = []

    def get_portfolios(self):
        return {"portfolios": [{"uuid": "other", "type": "CONSUMER"},
                               {"uuid": "abc", "type": "DEFAULT"}]}

    def get_portfolio_breakdown(self, uuid):
        self.breakdown_calls.append(uuid)
        return BREAKDOWN


def test_producer_uses_the_default_portfolio():
    client = FakeClient()
    producer = CoinbasePortfolio(client=client)
    producer.poll()
    values = producer.poll()
    assert client.breakdown_calls == ["abc", "abc"]
    assert values["pf.total"] == 5432.10


def test_view_rows_follow_the_watchlist_order():
    view = PortfolioView(load("spleen"))
    total, rows = view.holdings(snap(**parse_breakdown(BREAKDOWN)), NOW)
    assert total == 5432.10
    # USD isn't watched; XRP and JUP aren't held and show 0 rather than vanishing.
    assert rows == [("SOL", 1800.0), ("BTC", 3500.0), ("XRP", 0.0), ("JUP", 0.0)]


def test_jupiter_is_labelled_jup():
    data = {"breakdown": {"spot_positions": [
        {"asset": "JUPITER", "total_balance_crypto": 40.0, "total_balance_fiat": 14.35}]}}
    _total, rows = PortfolioView(load("spleen")).holdings(snap(**parse_breakdown(data)), NOW)
    assert ("JUP", 14.35) in rows


def canvas_for(view, snapshot):
    canvas = np.zeros((32, 128), dtype=bool)
    view.render(canvas, snapshot, NOW)
    return canvas


def test_stale_portfolio_shows_only_the_placeholder_total():
    view = PortfolioView(load("spleen"))
    stale = {k: Reading(k, v, 0.0, 60.0) for k, v in parse_breakdown(BREAKDOWN).items()}
    assert view.holdings(stale, NOW) is None
    canvas = canvas_for(view, stale)
    assert not canvas[:, view.SPLIT + 1 :].any()  # no coin rows


def test_total_drops_to_small_type_only_when_big_type_cannot_fit():
    view = PortfolioView(load("spleen"))
    small = canvas_for(view, snap(**{"pf.total": 581.37, "pf.positions": ()}))
    huge = canvas_for(view, snap(**{"pf.total": 1234567.89, "pf.positions": ()}))
    # 2x spleen is 16px tall, so the $581 total reaches row 15; 1x stops at 8.
    assert small[12:16, : view.SPLIT].any()
    assert not huge[10:16, : view.SPLIT].any()
    assert not huge[:17, view.SPLIT - 1].any()  # never runs into the divider


def test_view_renders_pixels():
    view = PortfolioView(load("spleen"))
    fb = np.zeros((4, 128), dtype=np.uint8)
    view.render_into(fb, snap(**parse_breakdown(BREAKDOWN)), NOW)
    assert fb.any()


def test_formatters():
    assert format_usd(999.5) == "$999.50"
    assert format_usd(12345.6) == "$12,346"


def test_warm_resolves_the_portfolio_before_any_poll():
    client = FakeClient()
    producer = CoinbasePortfolio(client=client)
    producer.warm()
    assert producer._uuid == "abc"
    assert client.breakdown_calls == []
