"""Papierhandel gegen die eigene Datenbank.

Das ist die Voreinstellung und sollte es mindestens ein paar Wochen bleiben.
Fuellungen zum letzten bekannten Kurs, mit konfigurierbarem Schlupf und
Gebuehr, damit die Zahlen nicht schoener aussehen als die Wirklichkeit.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select

from ..db import session_scope
from ..models import EquitySnapshot, Position
from ..prices import PriceProvider
from .base import Broker, BrokerPosition, OrderRequest, OrderResult

log = logging.getLogger(__name__)


class PaperBroker(Broker):
    name = "paper"
    supports_bracket = True

    def __init__(
        self,
        prices: PriceProvider,
        starting_equity: float = 10_000.0,
        slippage_bps: float = 8.0,
        fee_bps: float = 5.0,
    ) -> None:
        self.prices = prices
        self.starting_equity = starting_equity
        self.slippage_bps = slippage_bps
        self.fee_bps = fee_bps

    def submit(self, order: OrderRequest) -> OrderResult:
        price = order.limit_price or self.prices.last_price(
            order.symbol, asset_class=order.asset_class
        )
        if not price:
            return OrderResult(False, message=f"kein Kurs fuer {order.symbol}")
        direction = 1 if order.side == "buy" else -1
        fill = price * (1 + direction * self.slippage_bps / 10_000)
        fee = fill * order.quantity * self.fee_bps / 10_000

        with session_scope() as session:
            pos = session.scalar(
                select(Position).where(
                    Position.symbol == order.symbol,
                    Position.broker == self.name,
                    Position.closed_at.is_(None),
                )
            )
            signed = direction * order.quantity
            if pos is None:
                pos = Position(
                    symbol=order.symbol,
                    broker=self.name,
                    quantity=signed,
                    avg_price=fill,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    source_actors=[str(order.meta.get("actor", ""))],
                )
                session.add(pos)
            else:
                new_qty = pos.quantity + signed
                if pos.quantity * signed > 0:  # aufstocken
                    pos.avg_price = (
                        pos.avg_price * abs(pos.quantity) + fill * order.quantity
                    ) / abs(new_qty)
                else:  # reduzieren oder drehen
                    closed = min(abs(signed), abs(pos.quantity))
                    pnl_per_unit = (fill - pos.avg_price) * (1 if pos.quantity > 0 else -1)
                    pos.realized_pnl += pnl_per_unit * closed
                    if abs(new_qty) < 1e-9:
                        pos.closed_at = dt.datetime.now(dt.UTC)
                    elif new_qty * pos.quantity < 0:
                        pos.avg_price = fill
                pos.quantity = new_qty
                if order.stop_loss:
                    pos.stop_loss = order.stop_loss
                if order.take_profit:
                    pos.take_profit = order.take_profit
                actor = str(order.meta.get("actor", ""))
                if actor and actor not in (pos.source_actors or []):
                    pos.source_actors = [*(pos.source_actors or []), actor]
            session.flush()

        log.info(
            "Papierhandel: %s %s %.6f zu %.4f (Gebuehr %.2f)",
            order.side, order.symbol, order.quantity, fill, fee,
        )
        return OrderResult(
            accepted=True,
            broker_order_id=f"paper-{order.client_order_id}",
            status="filled",
            filled_qty=order.quantity,
            filled_price=fill,
            message="simuliert",
        )

    def positions(self) -> list[BrokerPosition]:
        out: list[BrokerPosition] = []
        with session_scope() as session:
            for pos in session.scalars(
                select(Position).where(
                    Position.broker == self.name, Position.closed_at.is_(None)
                )
            ):
                last = self.prices.last_price(pos.symbol) or pos.avg_price
                out.append(
                    BrokerPosition(
                        symbol=pos.symbol,
                        quantity=pos.quantity,
                        avg_price=pos.avg_price,
                        market_value=pos.quantity * last,
                        unrealized_pnl=(last - pos.avg_price) * pos.quantity,
                    )
                )
        return out

    def equity(self) -> float:
        realized = 0.0
        with session_scope() as session:
            for pos in session.scalars(select(Position).where(Position.broker == self.name)):
                realized += pos.realized_pnl
        unrealized = sum(p.unrealized_pnl for p in self.positions())
        return self.starting_equity + realized + unrealized

    def close_position(self, symbol: str) -> OrderResult:
        with session_scope() as session:
            pos = session.scalar(
                select(Position).where(
                    Position.symbol == symbol,
                    Position.broker == self.name,
                    Position.closed_at.is_(None),
                )
            )
            if not pos:
                return OrderResult(False, message="keine offene Position")
            qty = abs(pos.quantity)
            side = "sell" if pos.quantity > 0 else "buy"
        return self.submit(
            OrderRequest(
                client_order_id=f"close-{symbol}-{int(dt.datetime.now(dt.UTC).timestamp())}",
                symbol=symbol,
                side=side,
                quantity=qty,
            )
        )

    def snapshot(self) -> None:
        """Kapitalstand festhalten. Grundlage fuer Tagesverlust und Rueckgang."""
        positions = self.positions()
        with session_scope() as session:
            session.add(
                EquitySnapshot(
                    equity=self.equity(),
                    cash=self.starting_equity,
                    gross_exposure=sum(abs(p.market_value) for p in positions),
                    open_positions=len(positions),
                )
            )
