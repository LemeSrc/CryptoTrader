"""Broker-Schnittstelle. Alles, was der Bot ueber die Aussenwelt wissen muss."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OrderRequest:
    client_order_id: str
    symbol: str
    side: str
    quantity: float
    asset_class: str = "equity"
    order_type: str = "market"
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderResult:
    accepted: bool
    broker_order_id: str | None = None
    status: str = "submitted"
    filled_qty: float = 0.0
    filled_price: float | None = None
    message: str = ""


@dataclass
class BrokerPosition:
    symbol: str
    quantity: float
    avg_price: float
    market_value: float = 0.0
    unrealized_pnl: float = 0.0


class Broker(ABC):
    name = "base"
    supports_bracket = False

    @abstractmethod
    def submit(self, order: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def positions(self) -> list[BrokerPosition]: ...

    @abstractmethod
    def equity(self) -> float: ...

    def cancel_all(self) -> None:
        return None

    def close_position(self, symbol: str) -> OrderResult:
        return OrderResult(False, message="nicht unterstuetzt")

    def is_market_open(self, asset_class: str = "equity") -> bool:
        return True
