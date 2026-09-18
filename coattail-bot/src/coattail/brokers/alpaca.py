"""Alpaca fuer US-Aktien.

Passt zu diesem Bot, weil Kongress-Meldungen fast ausschliesslich US-Aktien
betreffen, Bruchstuecke moeglich sind und es eine vollwertige Papierumgebung
unter derselben API gibt. Der Wechsel von Papier auf echt ist eine Zeile in
der Konfiguration, deshalb steht davor eine Sicherheitsabfrage im CLI.

Ohne alpaca-py laeuft der Rest des Bots weiter, diese Klasse meldet dann nur,
dass sie nicht einsatzbereit ist.
"""

from __future__ import annotations

import datetime as dt
import logging

from .base import Broker, BrokerPosition, OrderRequest, OrderResult

log = logging.getLogger(__name__)


class AlpacaBroker(Broker):
    name = "alpaca"
    supports_bracket = True

    def __init__(self, key_id: str, secret_key: str, paper: bool = True) -> None:
        try:
            from alpaca.trading.client import TradingClient
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("alpaca-py fehlt: pip install 'coattail[brokers]'") from exc
        self.client = TradingClient(key_id, secret_key, paper=paper)
        self.paper = paper

    def submit(self, order: OrderRequest) -> OrderResult:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import (
            LimitOrderRequest,
            MarketOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )

        side = OrderSide.BUY if order.side == "buy" else OrderSide.SELL
        bracket = {}
        # Bruchstuecke vertragen sich nicht mit Klammerorders. In dem Fall
        # uebernimmt der Reconciler die Stopueberwachung.
        whole_shares = float(order.quantity).is_integer()
        if order.stop_loss and whole_shares:
            bracket["order_class"] = "bracket"
            bracket["stop_loss"] = StopLossRequest(stop_price=round(order.stop_loss, 2))
            if order.take_profit:
                bracket["take_profit"] = TakeProfitRequest(limit_price=round(order.take_profit, 2))

        common = dict(
            symbol=order.symbol,
            qty=order.quantity,
            side=side,
            time_in_force=TimeInForce.DAY,
            client_order_id=order.client_order_id,
            **bracket,
        )
        try:
            if order.order_type == "limit" and order.limit_price:
                req = LimitOrderRequest(limit_price=round(order.limit_price, 2), **common)
            else:
                req = MarketOrderRequest(**common)
            resp = self.client.submit_order(req)
        except Exception as exc:  # noqa: BLE001
            log.error("Alpaca lehnt Order ab: %s", exc)
            return OrderResult(False, message=str(exc))

        return OrderResult(
            accepted=True,
            broker_order_id=str(resp.id),
            status=str(getattr(resp, "status", "submitted")),
            filled_qty=float(getattr(resp, "filled_qty", 0) or 0),
            filled_price=float(getattr(resp, "filled_avg_price", 0) or 0) or None,
        )

    def positions(self) -> list[BrokerPosition]:
        try:
            raw = self.client.get_all_positions()
        except Exception as exc:  # noqa: BLE001
            log.error("Alpaca Positionen: %s", exc)
            return []
        return [
            BrokerPosition(
                symbol=p.symbol,
                quantity=float(p.qty),
                avg_price=float(p.avg_entry_price),
                market_value=float(p.market_value or 0),
                unrealized_pnl=float(p.unrealized_pl or 0),
            )
            for p in raw
        ]

    def equity(self) -> float:
        try:
            return float(self.client.get_account().equity)
        except Exception as exc:  # noqa: BLE001
            log.error("Alpaca Kontostand: %s", exc)
            return 0.0

    def cancel_all(self) -> None:
        try:
            self.client.cancel_orders()
        except Exception as exc:  # noqa: BLE001
            log.error("Alpaca Stornierung: %s", exc)

    def close_position(self, symbol: str) -> OrderResult:
        try:
            resp = self.client.close_position(symbol)
            return OrderResult(True, broker_order_id=str(resp.id), status="submitted")
        except Exception as exc:  # noqa: BLE001
            return OrderResult(False, message=str(exc))

    def is_market_open(self, asset_class: str = "equity") -> bool:
        if asset_class == "crypto":
            return True
        try:
            return bool(self.client.get_clock().is_open)
        except Exception:  # noqa: BLE001
            now = dt.datetime.now(dt.UTC)
            return now.weekday() < 5 and 13 <= now.hour < 21
