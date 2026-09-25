"""Laeufe, die mehr als eine Pipelinestufe anfassen."""

from __future__ import annotations

import datetime as dt
import logging
import threading

from sqlalchemy import func, select

from .app import App
from .db import session_scope, set_state
from .models import Actor, ActorStat, Disclosure, EquitySnapshot, Order, Signal
from .scoring.score import rescore_actor

log = logging.getLogger(__name__)


RESCORE_STATE = "rescore"
# Unterhalb dieses Anteils an Trades mit Kursdaten ist ein Lauf wertlos.
MIN_PRICE_COVERAGE = 0.5
_RESCORE_LOCK = threading.Lock()


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
    Nie zweimal gleichzeitig: ein zweiter Aufruf waehrend eines Laufs kehrt
    sofort zurueck.
    """
    out = {"bewertet": 0, "geeignet": 0}
    if not _RESCORE_LOCK.acquire(blocking=False):
        log.info("Bewertung laeuft bereits, dieser Aufruf entfaellt")
        return out
    try:
        return _rescore_all(app, only_source, out)
    finally:
        _RESCORE_LOCK.release()


def _rescore_all(app: App, only_source: str | None, out: dict[str, int]) -> dict[str, int]:
    with session_scope() as session:
        stmt = select(Actor.id).where(Actor.active.is_(True))
        if only_source:
            stmt = stmt.where(Actor.source == only_source)
        actor_ids = list(session.scalars(stmt))
    log.info("Bewerte %d Personen", len(actor_ids))
    _prefetch_prices(app, actor_ids)
    trades_total = 0
    trades_priced = 0
    # Eine Transaktion pro Person. Ein einziger Block ueber alle wuerde die
    # Datenbank fuer die gesamte Laufzeit sperren, und die Quellen, die
    # parallel schreiben wollen, liefen in 'database is locked'.
    for actor_id in actor_ids:
        if _prices_blocked(app):
            # Mit Luecken in den Kursen kaemen falsche Noten heraus, und die
            # wuerden bis zum naechsten Lauf ueber das Kopieren entscheiden.
            log.warning(
                "Bewertung angehalten nach %d von %d Personen, weil die Kursquelle drosselt. "
                "Neuer Versuch in einer Stunde.",
                out["bewertet"], len(actor_ids),
            )
            out["abgebrochen"] = 1
            return out
        name = str(actor_id)
        try:
            with session_scope() as session:
                actor = session.get(Actor, actor_id)
                if actor is None:
                    continue
                name = actor.name
                stat = rescore_actor(session, actor, app.prices, app.config.scoring)
                eligible = bool(stat.eligible)
                trades_total += int(stat.n_trades or 0)
                trades_priced += int(stat.n_closed or 0)
        except Exception as exc:  # noqa: BLE001
            log.warning("Bewertung von %s fehlgeschlagen: %s", name, exc)
            continue
        out["bewertet"] += 1
        out["geeignet"] += int(eligible)
        if out["bewertet"] % 10 == 0:
            log.info("Bewertung: %d von %d Personen", out["bewertet"], len(actor_ids))

    coverage = trades_priced / trades_total if trades_total else 1.0
    out["kursabdeckung_prozent"] = round(coverage * 100)
    log.info(
        "Bewertung fertig: %d geprueft, %d freigegeben, Kurse fuer %d%% der Trades",
        out["bewertet"], out["geeignet"], out["kursabdeckung_prozent"],
    )
    if coverage < MIN_PRICE_COVERAGE:
        # Ohne Kurse faellt jede Person mangels auswertbarer Trades durch. So
        # ein Lauf sieht fertig aus, ist aber wertlos und darf den Nachholjob
        # nicht fuer 36 Stunden stilllegen.
        log.warning(
            "Nur %d%% der Trades hatten Kurse. Lauf gilt nicht als fertig, "
            "neuer Versuch mit dem naechsten Nachholjob.",
            out["kursabdeckung_prozent"],
        )
        out["abgebrochen"] = 1
        return out
    if only_source is None:
        with session_scope() as session:
            set_state(
                session,
                RESCORE_STATE,
                {"finished_at": dt.datetime.now(dt.UTC).isoformat(), **out},
            )
    return out


def _prices_blocked(app: App) -> bool:
    blocked = getattr(app.prices, "yahoo_blocked", None)
    uses_alpaca = getattr(app.prices, "uses_alpaca", False)
    return bool(blocked and not uses_alpaca and blocked())


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
