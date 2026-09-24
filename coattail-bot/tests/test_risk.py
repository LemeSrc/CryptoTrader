from __future__ import annotations

import datetime as dt

from coattail.db import session_scope
from coattail.models import EquitySnapshot, Position, Signal
from coattail.risk.manager import RiskManager, reset_kill_switch, trip_kill_switch
from coattail.settings import AppConfig


def make_signal(**kw) -> Signal:
    defaults = dict(
        dedupe_key=kw.pop("key", "k1"),
        origin="test",
        symbol="AAA",
        asset_class="equity",
        side="buy",
        intent="open",
        event_at=dt.datetime.now(dt.UTC),
        conviction=0.5,
        actor_score=70.0,
        payload={},
    )
    defaults.update(kw)
    return Signal(**defaults)


def test_groesse_folgt_dem_stopabstand_nicht_dem_vorbild(config, prices):
    """Der Kern der Risikologik: 0.5 Prozent Kapitalrisiko bei 10 Prozent
    Stopabstand ergibt eine Position von 5 Prozent, nicht das, was das
    Vorbild investiert hat."""
    cfg = AppConfig()
    cfg.risk.equity_base = 10_000
    cfg.risk.risk_per_trade_pct = 0.5
    cfg.risk.max_position_pct = 100
    cfg.execution.trading_hours_only = False
    rm = RiskManager(cfg, prices)

    price = prices.last_price("AAA")
    with session_scope() as session:
        sig = make_signal(reference_price=price, stop_loss=price * 0.9, conviction=0.5, actor_score=50.0)
        session.add(sig)
        session.flush()
        decision = rm.evaluate(session, sig)

    assert decision.approved
    risiko = decision.quantity * (price - price * 0.9)
    assert abs(risiko - 25.0) < 0.5  # Faktor 0.5 bei Note 50


def test_deckel_pro_titel_greift_ueber_vorbilder_hinweg(config, prices):
    cfg = AppConfig()
    cfg.risk.equity_base = 10_000
    cfg.risk.max_ticker_exposure_pct = 2
    cfg.execution.trading_hours_only = False
    rm = RiskManager(cfg, prices)
    price = prices.last_price("AAA")

    with session_scope() as session:
        session.add(
            Position(symbol="AAA", broker="paper", quantity=200 / price * 1.0, avg_price=price)
        )
        session.flush()
        sig = make_signal(reference_price=price, stop_loss=price * 0.98)
        session.add(sig)
        session.flush()
        decision = rm.evaluate(session, sig)

    if decision.approved:
        assert decision.quantity * price <= 200.0 + 1e-6
    else:
        assert "ausgeschoepft" in decision.reason


def test_notaus_blockiert_alles(config, prices):
    cfg = AppConfig()
    cfg.execution.trading_hours_only = False
    rm = RiskManager(cfg, prices)
    with session_scope() as session:
        trip_kill_switch(session, "Test")
        sig = make_signal(reference_price=100.0, stop_loss=90.0)
        session.add(sig)
        session.flush()
        assert rm.evaluate(session, sig).approved is False
        reset_kill_switch(session)
        assert rm.evaluate(session, sig).approved is True


def test_tagesverlustgrenze_stoppt_neue_positionen(config, prices):
    cfg = AppConfig()
    cfg.risk.daily_loss_limit_pct = 3
    cfg.execution.trading_hours_only = False
    rm = RiskManager(cfg, prices)
    now = dt.datetime.now(dt.UTC)
    with session_scope() as session:
        session.add(EquitySnapshot(equity=10_000, taken_at=now - dt.timedelta(hours=6)))
        session.add(EquitySnapshot(equity=9_500, taken_at=now))
        sig = make_signal(reference_price=100.0, stop_loss=90.0)
        session.add(sig)
        session.flush()
        decision = rm.evaluate(session, sig)
    assert decision.approved is False
    assert "Tagesverlust" in decision.reason


def test_altes_signal_wird_verworfen(config, prices):
    cfg = AppConfig()
    cfg.execution.max_signal_age_minutes = 60
    cfg.execution.trading_hours_only = False
    rm = RiskManager(cfg, prices)
    with session_scope() as session:
        sig = make_signal(
            reference_price=100.0,
            stop_loss=90.0,
            event_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=2),
        )
        session.add(sig)
        session.flush()
        decision = rm.evaluate(session, sig)
    assert decision.approved is False
    assert "zu spaet" in decision.reason


def test_sperrliste_und_leerverkaufsschalter(config, prices):
    cfg = AppConfig()
    cfg.ticker_blocklist = ["AAA"]
    cfg.execution.trading_hours_only = False
    rm = RiskManager(cfg, prices)
    with session_scope() as session:
        sig = make_signal(reference_price=100.0, stop_loss=90.0)
        session.add(sig)
        session.flush()
        assert "Sperrliste" in rm.evaluate(session, sig).reason

        short = make_signal(key="k2", symbol="BBB", side="sell", reference_price=100.0, stop_loss=110.0)
        session.add(short)
        session.flush()
        assert "Leerverkaeufe" in rm.evaluate(session, short).reason


def test_ohne_stop_kein_hebelgeschaeft(config):
    class KeinAtr:
        def last_price(self, symbol, asset_class="equity"):
            return 100.0

        def atr(self, symbol, window=14, asset_class="equity"):
            return None

    cfg = AppConfig()
    cfg.execution.trading_hours_only = False
    cfg.risk.require_stop_for_leverage = True
    rm = RiskManager(cfg, KeinAtr())
    with session_scope() as session:
        sig = make_signal(symbol="BTC", asset_class="crypto", reference_price=100.0)
        session.add(sig)
        session.flush()
        decision = rm.evaluate(session, sig)
    assert decision.approved is False
    assert "Hebelmarkt" in decision.reason


def test_zeitstempel_aus_der_zukunft_wird_abgelehnt(config, prices):
    """Eine Quelle mit kaputtem Datum darf keine Order ausloesen."""
    cfg = AppConfig()
    cfg.execution.trading_hours_only = False
    rm = RiskManager(cfg, prices)
    with session_scope() as session:
        sig = make_signal(
            reference_price=100.0,
            stop_loss=90.0,
            event_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=3),
        )
        session.add(sig)
        session.flush()
        decision = rm.evaluate(session, sig)
    assert decision.approved is False
    assert "Zukunft" in decision.reason
