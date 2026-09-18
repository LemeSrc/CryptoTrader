from __future__ import annotations

import datetime as dt

from coattail.db import session_scope
from coattail.models import Actor
from coattail.scoring.evaluate import evaluate_disclosure_actor, evaluate_execution_actor
from coattail.scoring.score import eligibility, rescore_actor, score_from_evaluation
from coattail.settings import ScoringConfig

from .conftest import make_disclosure


def test_zu_wenige_trades_werden_abgelehnt(config, actor, prices):
    with session_scope() as session:
        for i in range(3):
            session.add(make_disclosure(actor.id, dt.date.today() - dt.timedelta(days=200 + i), seq=i))
    with session_scope() as session:
        stat = rescore_actor(session, session.get(Actor, actor.id), prices, ScoringConfig())
    assert stat.eligible is False
    assert any("Trades" in r for r in stat.reasons)


def test_langer_meldeverzug_schliesst_aus(config, actor, prices):
    cfg = ScoringConfig(min_trades=1, min_score=0.0, min_win_rate=0.0, max_disclosure_lag_days=30)
    with session_scope() as session:
        for i in range(20):
            session.add(
                make_disclosure(actor.id, dt.date.today() - dt.timedelta(days=300 + i), lag=90, seq=i)
            )
    with session_scope() as session:
        stat = rescore_actor(session, session.get(Actor, actor.id), prices, cfg)
    assert stat.median_lag_days >= 60
    assert stat.eligible is False
    assert any("Meldeverzoegerung" in r for r in stat.reasons)


def test_manuelle_sperre_schlaegt_jede_note(config, actor, prices):
    ev = evaluate_disclosure_actor([], prices, ScoringConfig())
    ev.win_rate_shrunk = 0.9
    ev.expectancy = 0.2
    ev.n_closed = 500
    blocked = Actor(source="x", external_id="y", name="z", manual_override="block")
    ok, reasons = eligibility(ev, 99.0, ScoringConfig(), blocked)
    assert ok is False
    assert reasons == ["manuell gesperrt"]


def test_manuelle_freigabe_umgeht_die_pruefung(config, prices):
    ev = evaluate_disclosure_actor([], prices, ScoringConfig())
    allowed = Actor(source="x", external_id="y", name="z", manual_override="allow")
    ok, _ = eligibility(ev, 0.0, ScoringConfig(), allowed)
    assert ok is True


def test_rundlauf_rechnung_bei_echten_fills(config, actor, prices):
    """Kauf zu 100, Verkauf zu 120 ergibt einen Gewinn von 20 Prozent."""
    with session_scope() as session:
        session.add(
            make_disclosure(
                actor.id, dt.date.today() - dt.timedelta(days=60), "buy",
                price=100.0, quantity=10, stop_loss=90.0, seq=1,
            )
        )
        session.add(
            make_disclosure(
                actor.id, dt.date.today() - dt.timedelta(days=30), "sell",
                price=120.0, quantity=10, seq=2,
            )
        )
    from sqlalchemy import select

    from coattail.models import Disclosure

    with session_scope() as session:
        rows = list(session.scalars(select(Disclosure)))
        ev = evaluate_execution_actor(rows, prices, ScoringConfig())
    assert ev.n_closed == 1
    assert abs(ev.expectancy - 0.2) < 1e-6
    assert ev.win_rate == 1.0
    assert ev.detail["avg_r"] == 2.0  # 20 Gewinn bei 10 Risiko


def test_score_belohnt_bessere_kennzahlen():
    from coattail.scoring.evaluate import Evaluation

    schwach = Evaluation(
        n_closed=30, win_rate_shrunk=0.42, expectancy=0.001, profit_factor=1.0,
        avg_win=0.03, avg_loss=-0.03, consistency=0.3, freshness=0.5, max_drawdown=0.4,
    )
    stark = Evaluation(
        n_closed=30, win_rate_shrunk=0.62, expectancy=0.04, profit_factor=2.1,
        avg_win=0.08, avg_loss=-0.03, consistency=0.9, freshness=1.0, max_drawdown=0.12,
        stop_usage_rate=0.9,
    )
    cfg = ScoringConfig()
    assert score_from_evaluation(stark, cfg) > score_from_evaluation(schwach, cfg) + 20
