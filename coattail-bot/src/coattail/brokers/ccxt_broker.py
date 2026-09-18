"""Kryptoboersen ueber ccxt.

Gedacht fuer die Trader-Seite: wer Hyperliquid-Fills kopiert, braucht einen
Platz zum Ausfuehren. ccxt spricht mit ueber hundert Boersen, der Code bleibt
derselbe.

Achtung bei Perpetuals: Hebel wird hier nicht automatisch gesetzt. Das muss
bewusst in der Konfiguration passieren, sonst kopiert der Bot irgendwann
zwanzigfach gehebelte Positionen, ohne dass es jemand wollte.
"""

from __future__ import annotations

import logging

from .base import Broker, BrokerPosition, OrderRequest, OrderResult

log = logging.getLogger(__name__)


class CcxtBroker(Broker):
    name = "ccxt"

    def __init__(
        self,
        exchange_id: str,
        api_key: str,
        secret: str,
        *,
        sandbox: bool = True,
        default_type: str = "spot",
        leverage: int | None = None,
    ) -> None:
        try:
            import ccxt
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("ccxt fehlt: pip install 'coattail[brokers]'") from exc
        cls = getattr(ccxt, exchange_id)
        self.exchange = cls(
            {
                "apiKey": api_key,
                "secret": secret,
                "enableRateLimit": True,
                "options": {"defaultType": default_type},
            }
        )
        if sandbox and self.exchange.has.get("sandbox"):
            self.exchange.set_sandbox_mode(True)
        self.leverage = leverage
        self.name = f"ccxt:{exchange_id}"

    def _market(self, symbol: str) -> str:
        if "/" in symbol:
            return symbol
        quote = "USDT"
        return f"{symbol}/{quote}"

    def submit(self, order: OrderRequest) -> OrderResult:
        market = self._market(order.symbol)
        params: dict = {}
        if self.leverage:
            try:
                self.exchange.set_leverage(self.leverage, market)
            except Exception as exc:  # noqa: BLE001
                log.warning("Hebel nicht setzbar: %s", exc)
        if order.stop_loss:
            params["stopLossPrice"] = order.stop_loss
        try:
            resp = self.exchange.create_order(
                market,
                "limit" if order.order_type == "limit" else "market",
                order.side,
                order.quantity,
                order.limit_price,
                params,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Boerse lehnt Order ab: %s", exc)
            return OrderResult(False, message=str(exc))
        return OrderResult(
            accepted=True,
            broker_order_id=str(resp.get("id")),
            status=str(resp.get("status") or "submitted"),
            filled_qty=float(resp.get("filled") or 0),
            filled_price=float(resp.get("average") or 0) or None,
        )

    def positions(self) -> list[BrokerPosition]:
        try:
            if self.exchange.has.get("fetchPositions"):
                raw = self.exchange.fetch_positions()
                return [
                    BrokerPosition(
                        symbol=p["symbol"],
                        quantity=float(p.get("contracts") or 0),
                        avg_price=float(p.get("entryPrice") or 0),
                        market_value=float(p.get("notional") or 0),
                        unrealized_pnl=float(p.get("unrealizedPnl") or 0),
                    )
                    for p in raw
                    if float(p.get("contracts") or 0) != 0
                ]
            balances = self.exchange.fetch_balance()
            out = []
            for asset, amount in (balances.get("total") or {}).items():
                if amount and asset not in ("USDT", "USD", "USDC"):
                    out.append(BrokerPosition(symbol=asset, quantity=float(amount), avg_price=0.0))
            return out
        except Exception as exc:  # noqa: BLE001
            log.error("Positionen nicht abrufbar: %s", exc)
            return []

    def equity(self) -> float:
        try:
            bal = self.exchange.fetch_balance()
            total = bal.get("total", {})
            for stable in ("USDT", "USD", "USDC"):
                if stable in total:
                    return float(total[stable])
            return float(sum(v for v in total.values() if isinstance(v, int | float)))
        except Exception as exc:  # noqa: BLE001
            log.error("Kontostand nicht abrufbar: %s", exc)
            return 0.0
