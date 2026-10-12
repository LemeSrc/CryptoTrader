from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from coattail.db import session_scope
from coattail.models import Actor, ActorStat, Disclosure, Signal
from coattail.pipeline.ingest import _store_trade
from coattail.pipeline.signals import build_signals
from coattail.settings import AppConfig, Secrets, SourceConfig
from coattail.sources.base import RawTrade


def raw(**kw) -> RawTrade:
    defaults = dict(
        source="demo",
        external_actor_id="p1",
        actor_name="Test Person",
        symbol="AAPL",
        side="buy",
        transaction_date=dt.date.today() - dt.timedelta(days=2),
        disclosed_at=dt.datetime.now(dt.UTC),
        amount_low=1001.0,
        amount_high=15000.0,
    )
    defaults.update(kw)
    return RawTrade(**defaults)


def test_derselbe_trade_aus_zwei_quellen_landet_einmal(config):
    """Die Quellen ueberschneiden sich absichtlich. Doppelte Orders duerfen
    daraus nicht entstehen."""
    assert _store_trade(raw(source="stockwatcher"))[0] is True
    assert _store_trade(raw(source="capitoltrades"))[0] is False

    with session_scope() as session:
        assert session.scalar(select(Disclosure).where(Disclosure.symbol == "AAPL")) is not None
        assert len(list(session.scalars(select(Disclosure)))) == 1


def test_unterschiedliche_trades_bleiben_getrennt(config):
    _store_trade(raw(symbol="AAPL"))
    _store_trade(raw(symbol="MSFT"))
    _store_trade(raw(symbol="AAPL", side="sell"))
    with session_scope() as session:
        assert len(list(session.scalars(select(Disclosure)))) == 3


def test_ohne_freigabe_kein_signal(config):
    _store_trade(raw())
    cfg = AppConfig()
    cfg.sources = [SourceConfig(name="posts_to_signals", kind="none", enabled=False)]
    assert build_signals(cfg, Secrets()) == 0
    with session_scope() as session:
        assert list(session.scalars(select(Signal))) == []


def test_freigegebene_person_erzeugt_signal(config):
    _store_trade(raw())
    with session_scope() as session:
        actor = session.scalar(select(Actor))
        session.add(
            ActorStat(
                actor_id=actor.id, score=72.0, eligible=True, n_closed=40,
                win_rate_shrunk=0.61, expectancy=0.03, reasons=["ok"],
            )
        )
    cfg = AppConfig()
    cfg.sources = [SourceConfig(name="posts_to_signals", kind="none", enabled=False)]
    assert build_signals(cfg, Secrets()) == 1

    with session_scope() as session:
        sig = session.scalar(select(Signal))
        assert sig.symbol == "AAPL"
        assert sig.side == "buy"
        assert sig.actor_score == 72.0
        assert 0 < sig.conviction <= 1

    # Zweiter Lauf darf nichts verdoppeln
    assert build_signals(cfg, Secrets()) == 0


def test_alte_meldung_erzeugt_kein_signal(config):
    alt = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
    _store_trade(raw(disclosed_at=alt, transaction_date=alt.date() - dt.timedelta(days=5)))
    with session_scope() as session:
        actor = session.scalar(select(Actor))
        session.add(ActorStat(actor_id=actor.id, score=80.0, eligible=True, reasons=["ok"]))
    cfg = AppConfig()
    cfg.execution.max_signal_age_minutes = 2880
    cfg.sources = [SourceConfig(name="posts_to_signals", kind="none", enabled=False)]
    assert build_signals(cfg, Secrets()) == 0


def test_haengengebliebene_signale_werden_wieder_eingereiht(config):
    """Nach einem Absturz zwischen Entscheidung und Buchung darf ein Signal
    nicht fuer immer auf 'pending' stehen bleiben."""
    from coattail.brokers.paper import PaperBroker
    from coattail.pipeline.execute import execute_pending
    from coattail.prices import PriceProvider
    from coattail.risk.manager import RiskManager

    class Fest(PriceProvider):
        def __init__(self):
            pass

        def last_price(self, symbol, asset_class="equity"):
            return 100.0

        def atr(self, symbol, window=14, asset_class="equity"):
            return 2.0

    alt = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    with session_scope() as session:
        session.add(
            Signal(
                dedupe_key="haenger",
                origin="test",
                symbol="AAA",
                side="buy",
                intent="open",
                event_at=dt.datetime.now(dt.UTC),
                status="pending",
                created_at=alt,
                actor_score=70.0,
                payload={},
            )
        )

    cfg = AppConfig()
    cfg.execution.mode = "dry_run"
    cfg.execution.trading_hours_only = False
    prices = Fest()
    execute_pending(cfg, PaperBroker(prices), RiskManager(cfg, prices))

    with session_scope() as session:
        sig = session.scalar(select(Signal).where(Signal.dedupe_key == "haenger"))
        assert sig.status == "simulated"


def test_limit_pro_titel_gilt_auch_innerhalb_eines_durchlaufs(config):
    """Regressionstest fuer den 02.10.2026: 47 Meldezeilen zum selben Kauf
    wurden im selben Durchlauf alle freigegeben, weil die Risikopruefung nur
    gebuchte Positionen sah. Aus 8 Prozent Limit wurden 235 Prozent."""
    from coattail.brokers.paper import PaperBroker
    from coattail.pipeline.execute import execute_pending
    from coattail.prices import PriceProvider
    from coattail.risk.manager import RiskManager

    class Fest(PriceProvider):
        def __init__(self):
            pass

        def last_price(self, symbol, asset_class="equity"):
            return 100.0

        def atr(self, symbol, window=14, asset_class="equity"):
            return 2.0

    with session_scope() as session:
        for i in range(5):
            session.add(
                Signal(
                    dedupe_key=f"zeile{i}",
                    origin="test",
                    symbol="KOD",
                    asset_class="equity",
                    side="buy",
                    intent="open",
                    event_at=dt.datetime.now(dt.UTC),
                    actor_score=70.0,
                    payload={},
                )
            )

    cfg = AppConfig()
    cfg.execution.mode = "dry_run"
    cfg.execution.trading_hours_only = False
    cfg.risk.equity_base = 10_000
    cfg.risk.max_position_pct = 5
    cfg.risk.max_ticker_exposure_pct = 8
    prices = Fest()
    stats = execute_pending(cfg, PaperBroker(prices), RiskManager(cfg, prices))

    # 500 plus 300 fuellen die 800 Dollar, der Rest wird abgelehnt.
    assert stats["rejected"] == 3
    with session_scope() as session:
        abgelehnt = list(session.scalars(select(Signal).where(Signal.status == "rejected")))
        assert all("ausgeschoepft" in s.reject_reason for s in abgelehnt)


def test_config_pfad_bei_installierter_venv_gefunden(tmp_path, monkeypatch):
    """Regressionstest fuer den Bug aus der ersten Serverinstallation: bei
    'pip install .' (kein editables Setup) landet settings.py tief in
    .venv/lib/pythonX.Y/site-packages/coattail/, und die alte Rechnung
    (drei Ebenen hoch) zeigte dann ins Leere. config.yaml wurde dadurch
    still ignoriert und der Bot lief nur mit den eingebauten Vorgaben,
    ganz ohne Datenquellen."""
    from coattail.settings import _find_default_config

    # Deploy-Layout nachstellen: config.yaml liegt neben dem cwd, in dem
    # 'coattail' aufgerufen wird (so wie unter systemd, WorkingDirectory
    # ist dort /opt/coattail).
    (tmp_path / "config").mkdir()
    expected = tmp_path / "config" / "config.yaml"
    expected.write_text("timezone: Europe/Berlin\n")

    monkeypatch.chdir(tmp_path)
    assert _find_default_config() == expected
