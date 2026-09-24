"""Rueckrechnung ueber die tatsaechlich gespeicherten Meldungen.

Bewusst einfach gehalten, und bewusst pessimistisch:

Einstieg am Meldetag, nicht am Handelstag. Frueher konnte niemand einsteigen,
also darf die Rechnung es auch nicht.

Ausstieg nach der festen Haltedauer oder wenn der Stop unterwegs unterschritten
wurde, gemessen an Tagesschlusskursen.

Gebuehren und Schlupf werden abgezogen. Ohne sie sieht jede Kopierstrategie
gut aus, und genau daran scheitern die meisten in der Praxis.

Was die Rechnung nicht kann: Ueberlebensverzerrung. Bewertet werden Personen,
die heute in der Datenbank stehen, mit Kennzahlen, die aus derselben Historie
stammen. Wer die Auswahl auf dieser Rechnung aufbaut, betrachtet sich beim
Zurueckschauen. Die Zahl taugt zum Vergleich von Einstellungen, nicht als
Renditeversprechen.
"""

from __future__ import annotations

import datetime as dt
import logging
import statistics
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ..db import session_scope
from ..models import Actor, ActorStat, Disclosure
from ..scoring import metrics

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)


def run_backtest(
    app: App,
    days: int = 365,
    hold_days: int | None = None,
    fee_bps: float = 10.0,
    slippage_bps: float = 15.0,
) -> dict[str, Any]:
    cfg = app.config.scoring
    hold = hold_days or max(cfg.horizons_days)
    start = dt.date.today() - dt.timedelta(days=days)
    stop_pct = app.config.risk.default_stop_atr_mult * 0.02  # Naeherung ohne ATR-Historie

    with session_scope() as session:
        eligible_ids = {
            row.actor_id
            for row in session.scalars(select(ActorStat).where(ActorStat.eligible.is_(True)))
        }
        if not eligible_ids:
            return {"Hinweis": "keine freigegebenen Personen, zuerst 'coattail score' ausfuehren"}
        rows = list(
            session.scalars(
                select(Disclosure)
                .where(Disclosure.actor_id.in_(eligible_ids))
                .order_by(Disclosure.disclosed_at.asc())
            )
        )
        names = {a.id: a.name for a in session.scalars(select(Actor))}

    returns: list[float] = []
    per_actor: dict[str, list[float]] = {}
    bench_returns: list[float] = []
    skipped = 0

    for d in rows:
        if d.side != "buy" or not d.symbol:
            continue
        entry_day = d.disclosed_at.date() if d.disclosed_at else d.transaction_date
        if not entry_day or entry_day < start:
            continue
        entry = app.prices.close_on(d.symbol, entry_day, asset_class=d.asset_class)
        if not entry:
            skipped += 1
            continue

        series = app.prices.history(d.symbol, asset_class=d.asset_class)
        if series is None:
            skipped += 1
            continue
        window = series[
            (series.index > str(entry_day)) & (series.index <= str(entry_day + dt.timedelta(days=hold)))
        ]
        if window.empty:
            skipped += 1
            continue

        stop_level = entry * (1 - stop_pct)
        hit = window[window <= stop_level]
        exit_price = float(hit.iloc[0]) if not hit.empty else float(window.iloc[-1])

        gross = exit_price / entry - 1
        cost = (fee_bps + slippage_bps) * 2 / 10_000
        net = gross - cost
        returns.append(net)
        per_actor.setdefault(names.get(d.actor_id, "?"), []).append(net)

        bench = app.prices.forward_return(cfg.benchmark, entry_day, hold)
        if bench is not None:
            bench_returns.append(bench)

    if not returns:
        return {"Hinweis": "keine auswertbaren Trades im Zeitraum", "uebersprungen": skipped}

    wins = sum(1 for r in returns if r > 0)
    best = sorted(per_actor.items(), key=lambda kv: statistics.fmean(kv[1]), reverse=True)[:3]
    worst = sorted(per_actor.items(), key=lambda kv: statistics.fmean(kv[1]))[:3]

    return {
        "Trades": len(returns),
        "Trefferquote": f"{wins / len(returns):.1%}",
        "Mittlere Rendite pro Trade": f"{statistics.fmean(returns):+.2%}",
        "Median": f"{statistics.median(returns):+.2%}",
        "Gewinnfaktor": f"{metrics.profit_factor(returns):.2f}",
        "Groesster Rueckgang": f"{metrics.max_drawdown(returns):.1%}",
        "Vergleichsindex im Schnitt": (
            f"{statistics.fmean(bench_returns):+.2%}" if bench_returns else "keine Daten"
        ),
        "Vorsprung": (
            f"{statistics.fmean(returns) - statistics.fmean(bench_returns):+.2%}"
            if bench_returns
            else "keine Daten"
        ),
        "Haltedauer": f"{hold} Tage",
        "Kosten je Rundlauf": f"{(fee_bps + slippage_bps) * 2 / 100:.2f} Prozent",
        "Beste Vorbilder": ", ".join(f"{n} ({statistics.fmean(v):+.1%})" for n, v in best),
        "Schwaechste Vorbilder": ", ".join(f"{n} ({statistics.fmean(v):+.1%})" for n, v in worst),
        "Ohne Kursdaten uebersprungen": skipped,
    }
