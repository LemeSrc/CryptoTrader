"""Kursabruf und Bewertungslauf: kein Titel darf die Bewertung aufhalten."""

from __future__ import annotations

import datetime as dt
import sys
import types

from coattail import prices as prices_mod
from coattail.db import get_state, session_scope
from coattail.models import Actor
from coattail.pipeline.ingest import _store_trade
from coattail.prices import PriceProvider, yahoo_symbol
from coattail.settings import AppConfig
from coattail.sources.base import RawTrade


def test_fehlender_titel_wird_nur_einmal_gesucht(tmp_path, monkeypatch):
    monkeypatch.setattr(prices_mod, "SYNTHETIC", False)
    p = PriceProvider(cache_dir=tmp_path)
    calls = []
    monkeypatch.setattr(p, "_equity_history", lambda s, y: calls.append(s) or None)
    for _ in range(10):
        assert p.history("SQ") is None
    assert calls == ["SQ"]
    # Die Liste ueberlebt einen Neustart
    p2 = PriceProvider(cache_dir=tmp_path)
    monkeypatch.setattr(p2, "_equity_history", lambda s, y: calls.append(s) or None)
    assert p2.history("SQ") is None
    assert calls == ["SQ"]


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            req = httpx.Request("GET", "https://data.alpaca.markets")
            raise httpx.HTTPStatusError("x", request=req, response=httpx.Response(self.status_code))


def test_alpaca_sammelabruf_mit_seiten_und_feedwechsel(tmp_path, monkeypatch):
    monkeypatch.setattr(prices_mod, "SYNTHETIC", False)
    calls = []
    pages = [
        _Resp({}, status=403),  # SIP nicht freigeschaltet
        _Resp({"bars": {"NVDA": [{"t": "2026-09-22T04:00:00Z", "c": 100.0}]}, "next_page_token": "p2"}),
        _Resp({"bars": {"NVDA": [{"t": "2026-09-23T04:00:00Z", "c": 102.0}],
                        "BRK.B": [{"t": "2026-09-23T04:00:00Z", "c": 402.0}]},
               "next_page_token": None}),
    ]

    def fake_get(self, url, params=None, **kwargs):
        calls.append(dict(params or {}))
        return pages.pop(0)

    monkeypatch.setattr("coattail.prices.HttpClient.get", fake_get)
    monkeypatch.setattr(prices_mod.time, "sleep", lambda s: None)
    p = PriceProvider(cache_dir=tmp_path, alpaca_key="k", alpaca_secret="s")
    yahoo = []
    monkeypatch.setattr(p, "_yahoo_history", lambda s, y: yahoo.append(s))
    assert p.prefetch(["NVDA", "BRK.B", "BRK/B"]) == 2
    # BRK/B ist kein Alpaca-Kuerzel und geht an Yahoo
    assert "BRK/B" not in calls[0]["symbols"]
    assert yahoo == ["BRK/B"]
    assert calls[0]["feed"] == "sip" and calls[1]["feed"] == "iex"
    assert calls[2]["page_token"] == "p2"
    assert p.history("NVDA").tolist() == [100.0, 102.0]
    assert p.history("BRK.B").iloc[-1] == 402.0


def test_drosselung_ist_kein_fehlender_titel(tmp_path, monkeypatch):
    monkeypatch.setattr(prices_mod, "SYNTHETIC", False)
    p = PriceProvider(cache_dir=tmp_path)
    calls = []

    def limited(symbol, years):
        calls.append(symbol)
        p._block_yahoo()
        raise prices_mod.RateLimited(symbol)

    monkeypatch.setattr(p, "_yahoo_history", limited)
    monkeypatch.setattr(p, "_stooq_history", lambda s: None)
    assert p.history("NVDA") is None
    assert not p._is_missing("equity:NVDA")
    assert p.yahoo_blocked()
    # Sammelabruf bricht sofort ab, statt tausend Titel ins Leere zu fragen
    monkeypatch.setattr(prices_mod.time, "sleep", lambda s: None)
    p2 = PriceProvider(cache_dir=tmp_path / "b")

    def limited2(symbol, years):
        calls.append(symbol)
        p2._block_yahoo()
        raise prices_mod.RateLimited(symbol)

    monkeypatch.setattr(p2, "_yahoo_history", limited2)
    calls.clear()
    assert p2.prefetch(["AAA", "BBB", "CCC"]) == 0
    assert calls == ["AAA"]
    assert not p2._is_missing("equity:AAA")


def test_bewertung_haelt_bei_drosselung_an(config, prices):
    from coattail import scheduler, tasks

    _store_trade(RawTrade(
        source="senate_ptr", external_actor_id="x", actor_name="John Boozman", symbol="VEA",
        side="buy", transaction_date=dt.date.today() - dt.timedelta(days=40),
        disclosed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=20),
    ))
    prices.yahoo_blocked = lambda: True
    app = types.SimpleNamespace(config=AppConfig(), prices=prices)
    result = tasks.rescore_all(app)
    assert result.get("abgebrochen") == 1 and result["bewertet"] == 0
    assert scheduler._scores_fresh() is False


def test_bewertung_laeuft_nie_doppelt(config, prices):
    from coattail import tasks

    app = types.SimpleNamespace(config=AppConfig(), prices=prices)
    tasks._RESCORE_LOCK.acquire()
    try:
        assert tasks.rescore_all(app) == {"bewertet": 0, "geeignet": 0}
    finally:
        tasks._RESCORE_LOCK.release()


def test_yahoo_schreibweise():
    assert yahoo_symbol("BRK.B") == "BRK-B"
    assert yahoo_symbol("AAPL") == "AAPL"


def test_bewertungslauf_meldet_sich_fertig(config, prices, monkeypatch):
    from coattail import scheduler, tasks

    _store_trade(RawTrade(
        source="senate_ptr", external_actor_id="x", actor_name="John Boozman", symbol="VEA",
        side="buy", transaction_date=dt.date.today() - dt.timedelta(days=40),
        disclosed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=20),
    ))
    fetched = []
    prices.prefetch = lambda symbols: fetched.append(set(symbols)) or 0
    app = types.SimpleNamespace(config=AppConfig(), prices=prices)

    assert scheduler._scores_fresh() is False
    result = tasks.rescore_all(app)
    assert result["bewertet"] == 1
    assert fetched and {"VEA", "SPY"} <= fetched[0]
    with session_scope() as session:
        assert get_state(session, tasks.RESCORE_STATE)["bewertet"] == 1
        assert session.query(Actor).count() == 1
    assert scheduler._scores_fresh() is True


def test_serie_leerer_antworten_gilt_als_ausfall(tmp_path, monkeypatch):
    """Ist Yahoo nicht erreichbar, liefert yfinance leere Tabellen statt eines
    Fehlers. Das darf nicht jeden Titel fuer zwoelf Stunden als fehlend markieren."""
    monkeypatch.setattr(prices_mod, "SYNTHETIC", False)
    monkeypatch.setattr(prices_mod.time, "sleep", lambda s: None)
    empty = types.SimpleNamespace(empty=True)
    fake_yf = types.SimpleNamespace(Ticker=lambda s: types.SimpleNamespace(history=lambda **k: empty))
    fake_exc = types.SimpleNamespace(YFRateLimitError=type("YFRateLimitError", (Exception,), {}))
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    monkeypatch.setitem(sys.modules, "yfinance.exceptions", fake_exc)
    p = PriceProvider(cache_dir=tmp_path)
    symbols = [f"T{i:03d}" for i in range(60)]
    assert p.prefetch(symbols) == 0
    assert p.yahoo_blocked()
    assert not any(p._is_missing(f"equity:{s}") for s in symbols)


def test_lauf_ohne_kurse_gilt_nicht_als_fertig(config, prices):
    """So sah die Nacht auf dem Server aus: Yahoo gedrosselt, jeder Titel ohne
    Kurs, alle Personen fallen durch. Das darf den Nachholjob nicht stilllegen."""
    from coattail import scheduler, tasks

    for i in range(3):
        _store_trade(RawTrade(
            source="senate_ptr", external_actor_id="x", actor_name="John Boozman",
            symbol=f"T{i}", side="buy", transaction_date=dt.date.today() - dt.timedelta(days=40 + i),
            disclosed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=20),
        ))
    prices.forward_return = lambda *a, **k: None
    app = types.SimpleNamespace(config=AppConfig(), prices=prices)
    result = tasks.rescore_all(app)
    assert result["bewertet"] == 1
    assert result["kursabdeckung_prozent"] == 0
    assert result.get("abgebrochen") == 1
    assert scheduler._scores_fresh() is False
