"""Gemeinsame Schnittstelle aller Datenquellen.

Eine Quelle liefert entweder Handelsereignisse (RawTrade) oder Beitraege
(RawPost). Alles Weitere passiert quellenunabhaengig in der Pipeline.
"""

from __future__ import annotations

import datetime as dt
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..settings import AppConfig, Secrets, SourceConfig

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RawTrade:
    source: str
    external_actor_id: str
    actor_name: str
    symbol: str | None
    side: str
    transaction_date: dt.date | None
    actor_type: str = "politician"
    asset_class: str = "equity"
    disclosed_at: dt.datetime | None = None
    amount_low: float | None = None
    amount_high: float | None = None
    price: float | None = None
    quantity: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    option_type: str | None = None
    party: str | None = None
    chamber: str | None = None
    state: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RawPost:
    platform: str
    external_id: str
    author: str
    text: str
    posted_at: dt.datetime
    author_id: str | None = None
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class Source(ABC):
    name: str = "base"
    kind: str = "disclosure"  # disclosure | post
    requires: tuple[str, ...] = ()

    def __init__(self, config: AppConfig, secrets: Secrets, source_config: SourceConfig) -> None:
        self.config = config
        self.secrets = secrets
        self.options = source_config.options
        self.source_config = source_config

    def available(self) -> tuple[bool, str]:
        missing = [key for key in self.requires if not self.secrets.has(key)]
        if missing:
            return False, "fehlende Zugangsdaten: " + ", ".join(missing)
        return True, "bereit"

    @abstractmethod
    def fetch(self, since: dt.datetime | None = None) -> Iterable[Any]:
        """Liefert RawTrade- oder RawPost-Objekte, neueste zuerst egal."""


class DisclosureSource(Source):
    kind = "disclosure"

    @abstractmethod
    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]: ...


class PostSource(Source):
    kind = "post"

    @abstractmethod
    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawPost]: ...


def normalize_side(value: str | None) -> str | None:
    """Jede Quelle schreibt 'Kauf' anders."""
    if not value:
        return None
    v = str(value).strip().lower()
    if v.startswith(("purchase", "buy", "p", "kauf", "long", "acquis")):
        return "buy"
    if v.startswith(("sale", "sell", "s", "verkauf", "short", "dispos", "exchange")):
        return "sell"
    if "partial" in v or "full" in v:
        return "sell"
    return None
