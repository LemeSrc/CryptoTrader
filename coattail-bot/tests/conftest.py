from __future__ import annotations

import datetime as dt

import pytest

from coattail.db import init_engine, session_scope
from coattail.models import Actor, Disclosure
from coattail.settings import AppConfig
from coattail.util import fingerprint


@pytest.fixture
def config(tmp_path) -> AppConfig:
    cfg = AppConfig()
    cfg.database_url = f"sqlite:///{tmp_path}/test.db"
    init_engine(cfg)
    return cfg


@pytest.fixture
def actor(config) -> Actor:
    with session_scope() as session:
        a = Actor(source="demo", external_id="t1", name="Test Person", actor_type="politician")
        session.add(a)
        session.flush()
        return a


class FakePrices:
    """Fester Kursverlauf statt Netzwerk. 1 Prozent pro Tag aufwaerts."""

    def __init__(self, start: float = 100.0, daily: float = 0.01) -> None:
        self.start = start
        self.daily = daily
        self.base = dt.date(2024, 1, 1)

    def close_on(self, symbol, day, asset_class="equity"):
        return self.start * (1 + self.daily) ** max((day - self.base).days, 0)

    def last_price(self, symbol, asset_class="equity"):
        return self.close_on(symbol, dt.date.today())

    def forward_return(self, symbol, start, horizon_days, asset_class="equity"):
        if symbol == "SPY":
            return 0.01 * horizon_days / 21
        return (1 + self.daily) ** horizon_days - 1

    def atr(self, symbol, window=14, asset_class="equity"):
        return self.last_price(symbol) * 0.02

    def history(self, symbol, asset_class="equity", years=4.0):
        return None


@pytest.fixture
def prices() -> FakePrices:
    return FakePrices()


def make_disclosure(actor_id: int, day: dt.date, side: str = "buy", lag: int = 10, **kw) -> Disclosure:
    return Disclosure(
        fingerprint=fingerprint(actor_id, day, side, kw.get("symbol", "AAA"), kw.get("seq", 0)),
        actor_id=actor_id,
        source="demo",
        symbol=kw.get("symbol", "AAA"),
        asset_class=kw.get("asset_class", "equity"),
        side=side,
        transaction_date=day,
        disclosed_at=dt.datetime.combine(day + dt.timedelta(days=lag), dt.time(12), tzinfo=dt.UTC),
        amount_low=kw.get("amount_low", 1001.0),
        amount_high=kw.get("amount_high", 15000.0),
        price=kw.get("price"),
        quantity=kw.get("quantity"),
        stop_loss=kw.get("stop_loss"),
    )
