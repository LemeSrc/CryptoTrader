"""Name aus der Konfiguration zu Quellenklasse."""

from __future__ import annotations

import logging

from ..settings import AppConfig, Secrets, SourceConfig
from .base import Source
from .congress import (
    CapitolTradesSource,
    FinnhubCongressSource,
    FmpCongressSource,
    HousePtrSource,
    QuiverSource,
    SenatePtrSource,
    StockWatcherSource,
)
from .demo import DemoSource
from .policy import FederalRegisterSource, UsaSpendingSource
from .sec import Sec13FSource, SecForm4Source
from .social import BlueskySource, MastodonApiSource, RssSource, XSource
from .traders import GenericLeaderboardSource, HyperliquidSource, InvoSource

log = logging.getLogger(__name__)

REGISTRY: dict[str, type[Source]] = {
    "stockwatcher": StockWatcherSource,
    "house_ptr": HousePtrSource,
    "senate_ptr": SenatePtrSource,
    "capitoltrades": CapitolTradesSource,
    "quiver": QuiverSource,
    "finnhub": FinnhubCongressSource,
    "fmp": FmpCongressSource,
    "sec_form4": SecForm4Source,
    "sec_13f": Sec13FSource,
    "invo": InvoSource,
    "hyperliquid": HyperliquidSource,
    "leaderboard": GenericLeaderboardSource,
    "bluesky": BlueskySource,
    "mastodon": MastodonApiSource,
    "x": XSource,
    "rss": RssSource,
    "federal_register": FederalRegisterSource,
    "usaspending": UsaSpendingSource,
    "demo": DemoSource,
}


# Eintraege mit diesem Typ sind reine Konfigurationsschalter (etwa die
# Post-Auswertung) und keine Datenquelle.
NON_SOURCES = {"none", ""}


def build_source(config: AppConfig, secrets: Secrets, sc: SourceConfig) -> Source | None:
    if sc.kind in NON_SOURCES and sc.name not in REGISTRY:
        return None
    cls = REGISTRY.get(sc.kind or sc.name)
    if cls is None:
        log.warning("Unbekannte Quelle '%s' in der Konfiguration", sc.name)
        return None
    return cls(config, secrets, sc)


def build_enabled_sources(config: AppConfig, secrets: Secrets) -> list[Source]:
    out: list[Source] = []
    for sc in config.enabled_sources():
        src = build_source(config, secrets, sc)
        if src is None:
            continue
        ok, reason = src.available()
        if not ok:
            log.warning("Quelle '%s' uebersprungen: %s", sc.name, reason)
            continue
        out.append(src)
    return out
