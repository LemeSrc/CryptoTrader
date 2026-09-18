"""Signale zu Auftraegen machen.

Der Ablauf ist bewusst in drei Phasen getrennt: entscheiden, abschicken,
buchen. Zwischen den Phasen ist keine Datenbanksitzung offen.

Der Grund ist nicht Schoenheit, sondern ein Fehler, der genau einmal auftreten
muss, um teuer zu werden: der Papierbroker schreibt selbst in die Datenbank.
Ruft man ihn aus einer offenen Sitzung heraus auf, sperrt SQLite sich selbst
aus, die aeussere Transaktion faellt zurueck, und der Auftrag ist abgeschickt,
aber nirgends verbucht. Getrennte Phasen schliessen das aus.

Der Trockenlauf geht denselben Weg und bricht erst in der letzten Zeile ab.
Was im Log steht, waere also auch so abgeschickt worden.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import select

from ..brokers.base import Broker, OrderRequest, OrderResult
from ..db import session_scope
from ..models import Order, Position, Signal
from ..notify.notifier import Notifier
from ..risk.manager import RiskManager
from ..settings import AppConfig
from ..util import fingerprint

log = logging.getLogger(__name__)

# Wie lange ein Signal in der Zwischenphase stehen darf, bevor es als
# abgebrochen gilt.
STALE_PENDING_MINUTES = 30


@dataclass
class Plan:
    signal_id: int
    client_order_id: str
    symbol: str
    side: str
    asset_class: str
    quantity: float
    order_type: str
    limit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_amount: float
    reference_price: float | None
    actor: str
    note: str | None
    intent: str = "open"


def execute_pending(
    config: AppConfig,
    broker: Broker,
    risk: RiskManager,
    notifier: Notifier | None = None,
    limit: int = 50,
) -> dict[str, int]:
    stats = {"executed": 0, "rejected": 0, "skipped": 0}
    dry = config.execution.mode == "dry_run"

    plans, closes = _decide(config, broker, risk, stats, notifier, limit)
    if not plans and not closes:
        return stats

    results: dict[int, tuple[Plan, OrderResult]] = {}
    for plan in plans:
        if dry:
            results[plan.signal_id] = (
                plan,
                OrderResult(True, status="dry_run", message="Trockenlauf"),
            )
            log.info(
                "Trockenlauf: %s %.6f %s zu etwa %.2f, Risiko %.2f, Stop %.2f",
                plan.side, plan.quantity, plan.symbol,
                plan.reference_price or 0, plan.risk_amount, plan.stop_loss or 0,
            )
            continue
        results[plan.signal_id] = (
            plan,
            broker.submit(
                OrderRequest(
                    client_order_id=plan.client_order_id,
                    symbol=plan.symbol,
                    side=plan.side,
                    quantity=plan.quantity,
                    asset_class=plan.asset_class,
                    order_type=plan.order_type,
                    limit_price=plan.limit_price,
                    stop_loss=plan.stop_loss,
                    take_profit=plan.take_profit,
                    meta={"actor": plan.actor, "signal": plan.signal_id},
                )
            ),
        )

    close_results: dict[int, OrderResult] = {}
    for signal_id, symbol in closes:
        if dry:
            log.info("Trockenlauf: Position %s wuerde geschlossen", symbol)
            close_results[signal_id] = OrderResult(True, status="dry_run")
        else:
            close_results[signal_id] = broker.close_position(symbol)

    _persist(broker, results, close_results, stats, dry, notifier)
    return stats


def _decide(
    config: AppConfig,
    broker: Broker,
    risk: RiskManager,
    stats: dict[str, int],
    notifier: Notifier | None,
    limit: int,
) -> tuple[list[Plan], list[tuple[int, str]]]:
    plans: list[Plan] = []
    closes: list[tuple[int, str]] = []
    rejections: list[tuple[Signal, str]] = []

    with session_scope() as session:
        # Nach einem Absturz zwischen Entscheidung und Buchung bleiben Signale
        # auf "pending" stehen. Sie waeren sonst fuer immer verloren, obwohl
        # keine Order dazu existiert.
        stale_cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=STALE_PENDING_MINUTES)
        stale = list(
            session.scalars(
                select(Signal).where(Signal.status == "pending", Signal.created_at < stale_cutoff)
            )
        )
        for signal in stale:
            has_order = session.scalar(select(Order).where(Order.signal_id == signal.id))
            signal.status = "executed" if has_order else "new"
        if stale:
            log.warning("%d haengengebliebene Signale wieder eingereiht", len(stale))

        pending = list(
            session.scalars(
                select(Signal)
                .where(Signal.status == "new")
                .order_by(Signal.event_at.asc())
                .limit(limit)
            )
        )
        for signal in pending:
            if config.execution.trading_hours_only and not broker.is_market_open(signal.asset_class):
                stats["skipped"] += 1
                continue

            if signal.intent == "close":
                pos = session.scalar(
                    select(Position).where(
                        Position.symbol == signal.symbol,
                        Position.broker == broker.name,
                        Position.closed_at.is_(None),
                    )
                )
                if not pos or abs(pos.quantity) <= 0:
                    signal.status = "skipped"
                    signal.reject_reason = "keine offene Position zum Schliessen"
                    stats["skipped"] += 1
                    continue
                signal.status = "pending"
                closes.append((signal.id, signal.symbol))
                continue

            decision = risk.evaluate(session, signal)
            if not decision.approved:
                signal.status = "rejected"
                signal.reject_reason = decision.reason
                stats["rejected"] += 1
                log.info("Abgelehnt: %s %s (%s)", signal.side, signal.symbol, decision.reason)
                rejections.append((signal, decision.reason))
                continue

            signal.status = "pending"
            plans.append(
                Plan(
                    signal_id=signal.id,
                    client_order_id=fingerprint("order", signal.dedupe_key)[:32],
                    symbol=signal.symbol,
                    side=signal.side,
                    asset_class=signal.asset_class,
                    quantity=decision.quantity,
                    order_type=config.execution.order_type,
                    limit_price=_limit_price(config, signal.side, decision.reference_price),
                    stop_loss=decision.stop_loss,
                    take_profit=decision.take_profit,
                    risk_amount=decision.risk_amount,
                    reference_price=decision.reference_price,
                    actor=str(signal.payload.get("actor") or signal.payload.get("author") or ""),
                    note=" | ".join(decision.notes) or None,
                )
            )

    if notifier:
        for signal, reason in rejections:
            notifier.rejection(signal, reason)
    return plans, closes


def _persist(
    broker: Broker,
    results: dict[int, tuple[Plan, OrderResult]],
    close_results: dict[int, OrderResult],
    stats: dict[str, int],
    dry: bool,
    notifier: Notifier | None,
) -> None:
    to_notify: list[tuple[Signal, Order]] = []

    with session_scope() as session:
        for signal_id, (plan, result) in results.items():
            signal = session.get(Signal, signal_id)
            order = Order(
                client_order_id=plan.client_order_id,
                signal_id=signal_id,
                broker=broker.name,
                broker_order_id=result.broker_order_id,
                symbol=plan.symbol,
                side=plan.side,
                quantity=plan.quantity,
                order_type=plan.order_type,
                limit_price=plan.limit_price,
                stop_loss=plan.stop_loss,
                take_profit=plan.take_profit,
                filled_qty=result.filled_qty,
                filled_price=result.filled_price,
                status="dry_run" if dry else (result.status if result.accepted else "rejected"),
                risk_amount=plan.risk_amount,
                note=plan.note if result.accepted else f"{plan.note or ''} | Broker: {result.message}",
            )
            session.add(order)
            if signal:
                if dry:
                    signal.status = "simulated"
                else:
                    signal.status = "executed" if result.accepted else "failed"
                    signal.reject_reason = None if result.accepted else result.message
                to_notify.append((signal, order))
            stats["executed" if result.accepted else "rejected"] += 1

        for signal_id, result in close_results.items():
            signal = session.get(Signal, signal_id)
            if signal:
                signal.status = ("simulated" if dry else "executed") if result.accepted else "failed"
                signal.reject_reason = None if result.accepted else result.message
            stats["executed" if result.accepted else "rejected"] += 1

    if notifier:
        for signal, order in to_notify:
            notifier.order(signal, order, dry_run=dry)


def _limit_price(config: AppConfig, side: str, reference: float | None) -> float | None:
    if config.execution.order_type != "limit" or not reference:
        return None
    offset = config.execution.limit_offset_bps / 10_000
    return reference * (1 + offset) if side == "buy" else reference * (1 - offset)
