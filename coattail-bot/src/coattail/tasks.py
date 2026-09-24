"""Laeufe, die mehr als eine Pipelinestufe anfassen."""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import func, select

from .app import App
from .db import session_scope, set_state
from .models import Actor, ActorStat, Disclosure, EquitySnapshot, Order, Signal
from .scoring.score import rescore_actor

log = logging.getLogger(__name__)


RESCORE_STATE = "rescore"


def _prefetch_prices(app: App, actor_ids: list[int]) -> None:
    """Alle benoetigten Aktienreihen vorab im Sammelabruf holen."""
    prefetch = getattr(app.prices, "prefetch", None)
    if prefetch is None or not actor_ids:
        return
    cutoff = dt.date.today() - dt.timedelta(days=app.config.scoring.lookback_days)
    with session_scope() as session:
        symbols = set(
            session.scalars(
                select(Disclosure.symbol)
                .where(
                    Disclosure.actor_id.in_(actor_ids),
                    Disclosure.asset_class == "equity",
                    Disclosure.transaction_date >= cutoff,
                )
                .distinct()
            )
        )
    symbols.add(app.config.scoring.benchmark)
    try:
        prefetch(symbols)
    except Exception as exc:  # noqa: BLE001
        log.warning("Sammelabruf der Kurse fehlgeschlagen, es geht einzeln weiter: %s", exc)


def rescore_all(app: App, only_source: str | None = None) -> dict[str, int]:
    """Alle beobachteten Personen neu bewerten.

    Laeuft nachts, weil dabei viele Kursreihen geladen werden. Der Cache im
    Kursanbieter sorgt dafuer, dass gaengige Titel nur einmal geholt werden.
    """
    out = {"bewertet": 0, "geeignet": 0}
    with session_scope() as session:
        stmt = select(Actor.id).where(Actor.active.is_(True))
        if only_source:
            stmt = stmt.where(Actor.source == only_source)
        actor_ids = list(session.scalars(stmt))
    log.info("Bewerte %d Personen", len(actor_ids))
    _prefetch_prices(app, actor_ids)
    # Eine Transaktion pro Person. Ein einziger Block ueber alle wuerde die
    # Datenbank fuer die gesamte Laufzeit sperren, und die Quellen, die
    # parallel schreiben wollen, liefen in 'database is locked'.
    for actor_id in actor_ids:
        name = str(actor_id)
        try:
            with session_scope() as session:
                actor = session.get(Actor, actor_id)
                if actor is None:
                    continue
                name = actor.name
                stat = rescore_actor(session, actor, app.prices, app.config.scoring)
                eligible = bool(stat.eligible)
        except Exception as exc:  # noqa: BLE001
            log.warning("Bewertung von %s fehlgeschlagen: %s", name, exc)
            continue
        out["bewertet"] += 1
        out["geeignet"] += int(eligible)
        if out["bewertet"] % 10 == 0:
            log.info("Bewertung: %d von %d Personen", out["bewertet"], len(actor_ids))
    log.info("Bewertung fertig: %(bewertet)d geprueft, %(geeignet)d freigegeben", out)
    if only_source is None:
        with session_scope() as session:
            set_state(
                session,
                RESCORE_STATE,
                {"finished_at": dt.datetime.now(dt.UTC).isoformat(), **out},
            )
    return out


def daily_report(app: App) -> list[str]:
    with session_scope() as session:
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
        orders = session.scalar(
            select(func.count()).select_from(Order).where(Order.created_at >= since)
        ) or 0
        signals = session.scalar(
            select(func.count()).select_from(Signal).where(Signal.created_at >= since)
        ) or 0
        rejected = session.scalar(
            select(func.count())
            .select_from(Signal)
            .where(Signal.created_at >= since, Signal.status == "rejected")
        ) or 0
        eligible = session.scalar(
            select(func.count())
            .select_from(ActorStat)
            .where(ActorStat.eligible.is_(True), ActorStat.computed_at >= since)
        ) or 0
        snaps = list(
            session.scalars(
                select(EquitySnapshot)
                .where(EquitySnapshot.taken_at >= since)
                .order_by(EquitySnapshot.taken_at.asc())
            )
        )

    change = 0.0
    if len(snaps) >= 2 and snaps[0].equity:
        change = (snaps[-1].equity / snaps[0].equity - 1) * 100
    equity = snaps[-1].equity if snaps else app.config.risk.equity_base

    lines = [
        f"Kapital: {equity:,.2f} ({change:+.2f} Prozent)",
        f"Signale: {signals}, davon abgelehnt {rejected}",
        f"Auftraege: {orders}",
        f"Freigegebene Vorbilder: {eligible}",
        f"Modus: {app.config.execution.mode} ueber {app.broker.name}",
    ]
    app.notifier.daily_summary(lines)
    for line in lines:
        log.info(line)
    return lines
