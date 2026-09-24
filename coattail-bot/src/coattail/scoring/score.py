"""Aus Kennzahlen wird eine Note und daraus eine Entscheidung.

Die Note ist bewusst nur ein Ranking-Hilfsmittel. Ueber das Kopieren
entscheiden die Ausschlusskriterien, und die sind hart: zu wenig Trades,
zu lange Meldeverzoegerung, negative Erwartung, kein Stop bei Hebel. Ein
hoher Punktwert hebt keines davon auf.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Actor, ActorStat, Disclosure
from ..prices import PriceProvider
from ..settings import ScoringConfig
from . import metrics
from .evaluate import Evaluation, evaluate_disclosure_actor, evaluate_execution_actor

log = logging.getLogger(__name__)

EXECUTION_SOURCES = {"invo", "hyperliquid", "leaderboard"}


def score_from_evaluation(ev: Evaluation, cfg: ScoringConfig) -> float:
    """0 bis 100. Jede Komponente wird erst auf 0..1 normiert, damit ein
    Ausreisser bei einer Kennzahl nicht die ganze Note kippt."""
    parts = {
        "win_rate": metrics.normalize(ev.win_rate_shrunk, 0.35, 0.70),
        "expectancy": metrics.normalize(ev.expectancy, -0.02, 0.08),
        "profit_factor": metrics.normalize(ev.profit_factor, 0.8, 2.5),
        "consistency": ev.consistency,
        "risk_discipline": _risk_discipline(ev),
        "freshness": ev.freshness,
    }
    weights = cfg.weights
    total = sum(weights.get(k, 0.0) for k in parts)
    if total <= 0:
        return 0.0
    raw = sum(parts[k] * weights.get(k, 0.0) for k in parts) / total
    penalty = metrics.normalize(ev.max_drawdown, 0.15, 0.60) * 0.15
    return round(max(0.0, min(1.0, raw - penalty)) * 100, 2)


def _risk_discipline(ev: Evaluation) -> float:
    """Belohnt kleine Verluste im Verhaeltnis zu den Gewinnen und gesetzte Stops."""
    if ev.avg_loss == 0:
        ratio = 1.0 if ev.avg_win > 0 else 0.0
    else:
        ratio = metrics.normalize(abs(ev.avg_win / ev.avg_loss), 0.6, 2.5)
    stop_bonus = ev.stop_usage_rate
    drawdown_ok = 1.0 - metrics.normalize(ev.max_drawdown, 0.1, 0.5)
    return 0.45 * ratio + 0.25 * stop_bonus + 0.30 * drawdown_ok


def eligibility(ev: Evaluation, score: float, cfg: ScoringConfig, actor: Actor) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if actor.manual_override == "block":
        return False, ["manuell gesperrt"]
    if actor.manual_override == "allow":
        return True, ["manuell freigegeben"]

    if ev.n_closed < cfg.min_trades:
        reasons.append(f"nur {ev.n_closed} auswertbare Trades, mindestens {cfg.min_trades} noetig")
    if ev.win_rate_shrunk < cfg.min_win_rate:
        reasons.append(
            f"Trefferquote {ev.win_rate_shrunk:.0%} unter Mindestwert {cfg.min_win_rate:.0%}"
        )
    if score < cfg.min_score:
        reasons.append(f"Punktwert {score:.1f} unter Mindestwert {cfg.min_score}")
    if ev.expectancy <= 0:
        reasons.append("negative Erwartung pro Trade")
    if ev.median_lag_days > cfg.max_disclosure_lag_days:
        reasons.append(
            f"Meldeverzoegerung {ev.median_lag_days:.0f} Tage, Grenze {cfg.max_disclosure_lag_days}"
        )
    if ev.max_drawdown > 0.6:
        reasons.append(f"Rueckschlag von {ev.max_drawdown:.0%} in der Historie")
    if ev.freshness < 0.15:
        reasons.append("seit langem nicht mehr aktiv")
    return (not reasons), reasons or ["alle Kriterien erfuellt"]


def rescore_actor(
    session: Session,
    actor: Actor,
    prices: PriceProvider,
    cfg: ScoringConfig,
) -> ActorStat:
    rows = list(
        session.scalars(select(Disclosure).where(Disclosure.actor_id == actor.id))
    )
    use_execution = actor.source in EXECUTION_SOURCES or actor.actor_type == "trader"
    ev = (
        evaluate_execution_actor(rows, prices, cfg)
        if use_execution
        else evaluate_disclosure_actor(rows, prices, cfg)
    )
    score = score_from_evaluation(ev, cfg)
    eligible, reasons = eligibility(ev, score, cfg, actor)

    stat = ActorStat(
        actor_id=actor.id,
        window_days=cfg.lookback_days,
        n_trades=ev.n_trades,
        n_closed=ev.n_closed,
        win_rate=round(ev.win_rate, 4),
        win_rate_shrunk=round(ev.win_rate_shrunk, 4),
        avg_win=round(ev.avg_win, 4),
        avg_loss=round(ev.avg_loss, 4),
        profit_factor=round(ev.profit_factor, 3),
        expectancy=round(ev.expectancy, 5),
        alpha_from_trade=round(ev.alpha_from_trade, 5),
        alpha_from_disclosure=round(ev.alpha_from_disclosure, 5),
        median_hold_days=round(ev.median_hold_days, 1),
        median_lag_days=round(ev.median_lag_days, 1),
        max_drawdown=round(ev.max_drawdown, 4),
        stop_usage_rate=round(ev.stop_usage_rate, 3),
        avg_risk_pct=round(ev.avg_risk_pct, 3),
        consistency=round(ev.consistency, 3),
        freshness=round(ev.freshness, 3),
        score=score,
        eligible=eligible,
        reasons=reasons,
        detail=ev.detail,
        computed_at=dt.datetime.now(dt.UTC),
    )
    session.add(stat)
    session.flush()
    return stat


def latest_stat(session: Session, actor_id: int) -> ActorStat | None:
    return session.scalar(
        select(ActorStat)
        .where(ActorStat.actor_id == actor_id)
        .order_by(ActorStat.computed_at.desc())
        .limit(1)
    )
