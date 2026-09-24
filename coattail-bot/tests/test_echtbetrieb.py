"""Tests fuer den Umstieg auf echte Daten: Personenabgleich ueber Quellen
hinweg, Ersatzweg fuer Capitol Trades, Boersenzeiten und Papierdepot."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from coattail.brokers.base import OrderRequest
from coattail.brokers.paper import PaperBroker
from coattail.db import session_scope
from coattail.market import us_market_open
from coattail.models import Actor, Disclosure, Post, Signal
from coattail.pipeline.ingest import _store_trade
from coattail.pipeline.signals import build_signals
from coattail.settings import AppConfig, Secrets, SourceConfig
from coattail.sources.base import RawTrade
from coattail.sources.congress import CapitolTradesSource, _ct_date, _ct_size
from coattail.sources.social import RssSource, XSource
from coattail.util import person_key

CT_HTML = """
<table class="q-table trades-table"><tbody>
<tr>
  <td><div class="q-fieldset politician-name"><a href="/politicians/P000197">Nancy Pelosi</a></div>
      <span class="q-field party">Democrat</span><span class="q-field chamber">House</span>
      <span class="q-field us-state-compact">CA</span></td>
  <td><div class="q-fieldset issuer-name"><a href="/issuers/1">NVIDIA Corp</a></div>
      <span class="q-field issuer-ticker">NVDA:US</span></td>
  <td><div>13:05</div><div>Yesterday</div></td>
  <td><div>2 Sept</div><div>2026</div></td>
  <td>days 21</td>
  <td>Spouse</td>
  <td><span class="tx-type">buy</span></td>
  <td><span class="trade-size">1M&ndash;5M</span></td>
  <td>$181.20</td>
</tr>
<tr>
  <td><div class="q-fieldset politician-name">
      <a href="/politicians/G000596">Marjorie Taylor Greene</a></div></td>
  <td><div class="q-fieldset issuer-name">Some Muni Bond</div>
      <span class="q-field issuer-ticker">N/A</span></td>
  <td>20 Aug2026</td><td>1 Aug2026</td><td>19</td><td>Self</td>
  <td><span class="tx-type">sell</span></td><td><span class="trade-size">1K–15K</span></td><td>N/A</td>
</tr>
</tbody></table>
"""


def _src(cls, name=None, **options):
    return cls(AppConfig(), Secrets(), SourceConfig(name=name or cls.name, kind=cls.name, options=options))


def test_personenschluessel_gleicht_schreibweisen_an():
    assert person_key("Nancy Pelosi") == "nancy_pelosi"
    assert person_key("Pelosi, Nancy") == "nancy_pelosi"
    assert person_key("Hon. Nancy P. Pelosi") == "nancy_pelosi"
    assert person_key("Rick W. Allen, Jr.") == "rick_allen"
    assert person_key("Sen. José Ruiz") == "jose_ruiz"
    assert person_key("") == ""


def test_dieselbe_person_aus_zwei_feeds_ist_eine_person(config):
    """Ohne Abgleich waere die Historie auf mehrere Personen verteilt und
    keine davon kaeme ueber die Mindestzahl an Trades."""
    day = dt.date.today() - dt.timedelta(days=5)
    common = dict(side="buy", transaction_date=day, disclosed_at=dt.datetime.now(dt.UTC))
    _store_trade(RawTrade(source="capitoltrades", external_actor_id="P000197",
                          actor_name="Nancy Pelosi", symbol="NVDA", **common))
    _store_trade(RawTrade(source="quiver", external_actor_id="nancy_pelosi",
                          actor_name="Hon. Nancy Pelosi", symbol="NVDA", **common))
    _store_trade(RawTrade(source="fmp", external_actor_id="x",
                          actor_name="Pelosi, Nancy", symbol="AAPL", **common))
    with session_scope() as session:
        actors = list(session.scalars(select(Actor)))
        assert len(actors) == 1
        assert actors[0].source == "congress"
        assert len(list(session.scalars(select(Disclosure)))) == 2


def test_capitoltrades_tabellenseite():
    src = _src(CapitolTradesSource)
    today = dt.date(2026, 9, 24)
    trades = list(src.parse_html(CT_HTML, today=today))
    assert len(trades) == 2
    first = trades[0]
    assert first.actor_name == "Nancy Pelosi"
    assert first.symbol == "NVDA"
    assert first.side == "buy"
    assert first.transaction_date == dt.date(2026, 9, 2)
    assert first.disclosed_at.date() == dt.date(2026, 9, 23)
    assert (first.amount_low, first.amount_high) == (1e6, 5e6)
    assert first.price == 181.20
    assert first.party == "Democrat"
    second = trades[1]
    assert second.symbol is None  # Anleihe ohne Kuerzel, wird spaeter verworfen
    assert second.side == "sell"
    assert second.disclosed_at.date() == dt.date(2026, 8, 20)


def test_capitoltrades_datum_und_groesse():
    today = dt.date(2026, 9, 24)
    assert _ct_date("Today", today) == today
    assert _ct_date("23 Oct2025", today) == dt.date(2025, 10, 23)
    assert _ct_date("2 Sept 2026", today) == dt.date(2026, 9, 2)
    assert _ct_date("kein Datum", today) is None
    assert _ct_size("15K–50K") == (15_000, 50_000)
    assert _ct_size("") == (None, None)


def test_capitoltrades_faellt_auf_tabellenseite_zurueck(monkeypatch):
    src = _src(CapitolTradesSource, pages=1)
    calls = {"html": 0}

    def no_json(self, *a, **k):
        return iter(())

    def fake_html(self, client, pages, since):
        calls["html"] += 1
        yield from self.parse_html(CT_HTML, today=dt.date(2026, 9, 24))

    monkeypatch.setattr(CapitolTradesSource, "_fetch_json", no_json)
    monkeypatch.setattr(CapitolTradesSource, "_fetch_html", fake_html)
    assert len(list(src.fetch(dt.datetime(2026, 1, 1, tzinfo=dt.UTC)))) == 2
    assert calls["html"] == 1


def test_boersenzeiten_new_york():
    # Mittwoch, 23.09.2026, 15:00 UTC = 11:00 in New York
    assert us_market_open(dt.datetime(2026, 9, 23, 15, 0, tzinfo=dt.UTC))
    # derselbe Tag vor der Eroeffnung und nach Schluss
    assert not us_market_open(dt.datetime(2026, 9, 23, 13, 0, tzinfo=dt.UTC))
    assert not us_market_open(dt.datetime(2026, 9, 23, 20, 30, tzinfo=dt.UTC))
    # Samstag und Thanksgiving
    assert not us_market_open(dt.datetime(2026, 9, 26, 15, 0, tzinfo=dt.UTC))
    assert not us_market_open(dt.datetime(2026, 11, 26, 16, 0, tzinfo=dt.UTC))


class _Fest:
    def __init__(self, p: float = 100.0) -> None:
        self.p = p

    def last_price(self, symbol, asset_class="equity"):
        return self.p


def test_papierdepot_oeffnet_nach_stop_erneut(config):
    """Nach einem Ausstieg muss derselbe Titel wieder kaufbar sein, der
    realisierte Gewinn aus der ersten Runde bleibt erhalten."""
    prices = _Fest()
    broker = PaperBroker(prices, starting_equity=10_000, slippage_bps=0, fee_bps=0)
    broker.submit(OrderRequest(client_order_id="a", symbol="NVDA", side="buy", quantity=10))
    prices.p = 110.0
    broker.close_position("NVDA")
    assert broker.positions() == []
    result = broker.submit(OrderRequest(client_order_id="b", symbol="NVDA", side="buy", quantity=5))
    assert result.accepted
    positions = broker.positions()
    assert len(positions) == 1 and abs(positions[0].quantity - 5) < 1e-9
    assert abs(broker.equity() - 10_100) < 1e-6


def test_papierdepot_zieht_gebuehren_ab(config):
    broker = PaperBroker(_Fest(100.0), starting_equity=10_000, slippage_bps=0, fee_bps=10)
    broker.submit(OrderRequest(client_order_id="a", symbol="XYZ", side="buy", quantity=10))
    # 1000 Einsatz, 10 Basispunkte Gebuehr
    assert abs(broker.equity() - 9_999.0) < 1e-6


def test_papierdepot_krypto_ist_immer_offen():
    broker = PaperBroker(_Fest(), starting_equity=1_000)
    assert broker.is_market_open("crypto")


def test_x_loest_handles_einmal_auf(monkeypatch):
    src = _src(XSource, users=["realDonaldTrump"], limit=10)
    src.secrets = Secrets(x_bearer_token="t")
    seen: list[str] = []

    def fake_json(self, url, **kwargs):
        seen.append(url)
        if "/users/by/username/" in url:
            return {"data": {"id": "25073877"}}
        return {"data": [{"id": "1", "text": "Big news for $LMT", "created_at": "2026-09-24T12:00:00Z"}]}

    monkeypatch.setattr("coattail.sources.social.HttpClient.json", fake_json)
    first = list(src.fetch(None))
    second = list(src.fetch(None))
    assert len(first) == 1 and len(second) == 1
    assert first[0].author_id == "25073877"
    assert sum("/users/by/username/" in u for u in seen) == 1


def test_konfigname_wird_quellenname():
    src = _src(XSource, name="x_politik", users=["a"])
    assert src.name == "x_politik"


def test_gleicher_post_ueber_zwei_wege_ein_signal(config):
    now = dt.datetime.now(dt.UTC)
    text = "We will approve and expand the big contract with $LMT"
    with session_scope() as session:
        session.add(Post(platform="truthsocial", external_id="1", author="realDonaldTrump",
                         text=text, posted_at=now - dt.timedelta(minutes=3)))
        session.add(Post(platform="rss", external_id="https://x/1", author="realDonaldTrump",
                         text=text, posted_at=now - dt.timedelta(minutes=2)))
    cfg = AppConfig()
    cfg.sources = [SourceConfig(name="posts_to_signals", kind="none", enabled=True,
                                options={"use_llm": False, "min_conviction": 0.1})]
    build_signals(cfg, Secrets())
    with session_scope() as session:
        rows = list(session.scalars(select(Signal).where(Signal.symbol == "LMT")))
        assert len(rows) == 1


def test_rss_ueber_eigenen_client(monkeypatch):
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>Tariffs on steel</title><link>https://example.org/1</link>
    <guid>1</guid><pubDate>Wed, 23 Sep 2026 14:00:00 GMT</pubDate>
    <description>New tariff package announced</description></item>
    </channel></rss>"""

    class Resp:
        content = feed

        def raise_for_status(self):
            return None

    monkeypatch.setattr("coattail.sources.social.HttpClient.get", lambda self, url, **k: Resp())
    src = _src(RssSource, feeds=[{"url": "https://example.org/feed", "label": "realDonaldTrump"}])
    posts = list(src.fetch(dt.datetime(2026, 9, 1, tzinfo=dt.UTC)))
    assert len(posts) == 1
    assert posts[0].author == "realDonaldTrump"
    assert "tariff" in posts[0].text.lower()
