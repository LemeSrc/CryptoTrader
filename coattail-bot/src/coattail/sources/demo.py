"""Kuenstliche Daten, damit der Bot ohne einen einzigen API-Key startbar ist.

Nutzen: Pipeline, Bewertung, Risikologik und Ausfuehrung lassen sich komplett
durchspielen, bevor man ueberhaupt Zugangsdaten besorgt. Auch praktisch fuer
Tests in der CI.
"""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Iterable

from .base import DisclosureSource, RawTrade

_ACTORS = [
    ("demo_solid", "Dana Solid", 0.68),
    ("demo_mixed", "Max Mittel", 0.52),
    ("demo_weak", "Willi Wackel", 0.36),
]
_SYMBOLS = ["AAPL", "MSFT", "NVDA", "LMT", "XOM", "JPM", "PFE", "GOOGL"]


class DemoSource(DisclosureSource):
    name = "demo"

    def available(self) -> tuple[bool, str]:
        return True, "bereit"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]:
        rng = random.Random(int(self.options.get("seed", 7)))
        days = int(self.options.get("history_days", 540))
        per_actor = int(self.options.get("trades_per_actor", 40))
        fresh = int(self.options.get("fresh_trades", 2))
        today = dt.date.today()

        # Ein paar taufrische Meldungen, damit run-once tatsaechlich bis zur
        # Order durchlaeuft. Ohne sie ist alles aelter als max_signal_age und
        # wird zu Recht verworfen.
        now = dt.datetime.now(dt.UTC)
        for i in range(fresh):
            yield RawTrade(
                source=self.name,
                external_actor_id=_ACTORS[0][0],
                actor_name=_ACTORS[0][1],
                actor_type="politician",
                symbol=_SYMBOLS[i % len(_SYMBOLS)],
                side="buy",
                transaction_date=today - dt.timedelta(days=3),
                disclosed_at=now - dt.timedelta(minutes=30 + i),
                amount_low=15001.0,
                amount_high=50000.0,
                raw={"demo": "frisch"},
            )

        for ext_id, name, skill in _ACTORS:
            for i in range(per_actor):
                tx = today - dt.timedelta(days=rng.randint(1, days))
                lag = rng.randint(3, 42)
                yield RawTrade(
                    source=self.name,
                    external_actor_id=ext_id,
                    actor_name=name,
                    actor_type="politician",
                    symbol=rng.choice(_SYMBOLS),
                    side="buy" if rng.random() < 0.7 else "sell",
                    transaction_date=tx,
                    disclosed_at=dt.datetime.combine(
                        min(tx + dt.timedelta(days=lag), today), dt.time(12, 0), tzinfo=dt.UTC
                    ),
                    amount_low=1001.0,
                    amount_high=15000.0,
                    raw={"demo_skill": skill, "seq": i},
                )
