"""Kursabruf und Bewertungslauf: kein Titel darf die Bewertung aufhalten."""

from __future__ import annotations

import datetime as dt
import sys
import types

import pandas as pd

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


def test_sammelabruf_fuellt_den_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(prices_mod, "SYNTHETIC", False)
    monkeypatch.setattr(prices_mod.time, "sleep", lambda s: None)
    idx = pd.to_datetime(["2026-09-21", "2026-09-22", "2026-09-23"])
    cols = pd.MultiIndex.from_product([["NVDA", "BRK-B"], ["Open", "Close"]])
    frame = pd.DataFrame(
        [[1, 100.0, 1, 400.0], [1, 101.0, 1, 401.0], [1, 102.0, 1, 402.0]], index=idx, columns=cols
    )
    requested = []

    def fake_download(tickers, **kwargs):
        requested.append(list(tickers))
        return frame

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=fake_download))
    p = PriceProvider(cache_dir=tmp_path)
    assert p.prefetch(["NVDA", "BRK.B", "NVDA"]) == 2
    assert sorted(requested[0]) == ["BRK-B", "NVDA"]
    monkeypatch.setattr(p, "_equity_history", lambda s, y: (_ for _ in ()).throw(AssertionError(s)))
    assert p.history("BRK.B").iloc[-1] == 402.0
    assert p.history("NVDA").iloc[-1] == 102.0
    # zweiter Aufruf: alles schon da, kein neuer Abruf
    assert p.prefetch(["NVDA", "BRK.B"]) == 0
    assert len(requested) == 1


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
