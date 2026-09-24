"""Aufraeumen zwischen den Durchlaeufen.

Drei Aufgaben: Stops und Ziele pruefen, den Kapitalstand festhalten und den
Notaus ausloesen, wenn eine Grenze gerissen ist. Der letzte Punkt ist der
Grund, warum das hier oefter laeuft als die Datenabfrage. Eine Position kann
zwischen zwei Abfragen durch den Stop fallen, und dann soll sie zu sein.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select

from ..brokers.base import Broker
from ..brokers.paper import PaperBroker
from ..db import session_scope
from ..models import EquitySnapshot, Position
from ..prices import PriceProvider
from ..risk.manager import RiskManager, trip_kill_switch
from ..settings import AppConfig

log = logging.getLogger(__name__)


def reconcile(
    config: AppConfig,
    broker: Broker,
    prices: PriceProvider,
    risk: RiskManager,
    notifier=None,
) -> dict[str, int]:
    out = {"stops_hit": 0, "targets_hit": 0}

    if isinstance(broker, PaperBroker):
        out.update(_check_exits(broker, prices))
        broker.snapshot()
    else:
        _snapshot_live(broker)

    with session_scope() as session:
        day = risk.day_pnl_pct(session)
        dd = risk.drawdown_pct(session)
        if day <= -abs(config.risk.daily_loss_limit_pct):
            trip_kill_switch(session, f"Tagesverlust {day:.2f} Prozent")
            if notifier:
                notifier.kill_switch(f"Tagesverlust {day:.2f} Prozent")
        elif dd >= config.risk.max_drawdown_pct:
            trip_kill_switch(session, f"Rueckgang {dd:.2f} Prozent")
            if notifier:
                notifier.kill_switch(f"Rueckgang {dd:.2f} Prozent")
    return out


def _check_exits(broker: PaperBroker, prices: PriceProvider) -> dict[str, int]:
    hits = {"stops_hit": 0, "targets_hit": 0}
    with session_scope() as session:
        open_positions = list(
            session.scalars(
                select(Position).where(
                    Position.broker == broker.name, Position.closed_at.is_(None)
                )
            )
        )
        targets = [
            (p.symbol, p.asset_class or "equity", p.quantity, p.stop_loss, p.take_profit)
            for p in open_positions
        ]

    for symbol, asset_class, qty, stop, take in targets:
        # Ausserhalb der Handelszeit gibt es keinen neuen Kurs und keine
        # Fuellung. Ein Stop wird dann zur Eroeffnung geprueft, wie beim Broker.
        if not broker.is_market_open(asset_class):
            continue
        last = prices.last_price(symbol, asset_class=asset_class)
        if not last:
            continue
        long = qty > 0
        if stop and ((long and last <= stop) or (not long and last >= stop)):
            log.info("Stop erreicht: %s bei %.4f", symbol, last)
            broker.close_position(symbol)
            hits["stops_hit"] += 1
        elif take and ((long and last >= take) or (not long and last <= take)):
            log.info("Ziel erreicht: %s bei %.4f", symbol, last)
            broker.close_position(symbol)
            hits["targets_hit"] += 1
    return hits


def _snapshot_live(broker: Broker) -> None:
    positions = broker.positions()
    with session_scope() as session:
        session.add(
            EquitySnapshot(
                equity=broker.equity(),
                gross_exposure=sum(abs(p.market_value) for p in positions),
                open_positions=len(positions),
                taken_at=dt.datetime.now(dt.UTC),
            )
        )
