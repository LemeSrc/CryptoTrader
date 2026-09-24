from __future__ import annotations

from coattail.brokers.base import OrderRequest
from coattail.brokers.paper import PaperBroker
from coattail.nlp.classifier import RuleClassifier


def test_regeln_erkennen_kuerzel_und_richtung():
    r = RuleClassifier()
    bullisch = r.classify("We will approve and expand the contract with $LMT", "POTUS")
    assert any(s.symbol == "LMT" and s.side == "buy" for s in bullisch)

    baerisch = r.classify("We will ban and sanction $NVDA chip exports", "POTUS")
    assert any(s.symbol == "NVDA" and s.side == "sell" for s in baerisch)


def test_regeln_schweigen_ohne_bezug():
    r = RuleClassifier()
    assert r.classify("Thanks to everyone who came out to the town hall today", "Senator") == []


def test_themen_treffen_den_branchenkorb():
    r = RuleClassifier()
    out = r.classify("New tariff package announced on imported goods", "POTUS")
    assert any(s.symbol == "XLI" and s.side == "sell" for s in out)


def test_papierbroker_bucht_gewinn_beim_schliessen(config):
    class Fest:
        def __init__(self):
            self.p = 100.0

        def last_price(self, symbol, asset_class="equity"):
            return self.p

    prices = Fest()
    broker = PaperBroker(prices, starting_equity=10_000, slippage_bps=0, fee_bps=0)

    broker.submit(OrderRequest(client_order_id="a", symbol="XYZ", side="buy", quantity=10))
    assert len(broker.positions()) == 1
    assert abs(broker.equity() - 10_000) < 1e-6

    prices.p = 120.0
    assert abs(broker.equity() - 10_200) < 1e-6

    broker.close_position("XYZ")
    assert broker.positions() == []
    assert abs(broker.equity() - 10_200) < 1e-6


def test_papierbroker_haelt_sich_an_die_seite(config):
    class Fest:
        def last_price(self, symbol, asset_class="equity"):
            return 50.0

    broker = PaperBroker(Fest(), starting_equity=1_000, slippage_bps=0, fee_bps=0)
    broker.submit(OrderRequest(client_order_id="a", symbol="ABC", side="buy", quantity=4))
    broker.submit(OrderRequest(client_order_id="b", symbol="ABC", side="sell", quantity=1))
    positions = broker.positions()
    assert len(positions) == 1
    assert abs(positions[0].quantity - 3) < 1e-9
