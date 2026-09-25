"""Laufzeitkontext. Einmal bauen, ueberall weiterreichen."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .brokers.base import Broker
from .brokers.factory import build_broker
from .db import init_engine
from .logging_setup import setup_logging
from .notify.notifier import Notifier
from .prices import PriceProvider
from .risk.manager import RiskManager
from .settings import AppConfig, Secrets, get_secrets, load_config

log = logging.getLogger(__name__)


@dataclass
class App:
    config: AppConfig
    secrets: Secrets
    prices: PriceProvider
    broker: Broker
    risk: RiskManager
    notifier: Notifier

    @classmethod
    def create(cls, config_path: str | Path | None = None) -> App:
        config = load_config(config_path)
        secrets = get_secrets()
        setup_logging(config.log_level)
        init_engine(config)
        prices = PriceProvider(
            alpaca_key=secrets.alpaca_key_id, alpaca_secret=secrets.alpaca_secret_key
        )
        broker = build_broker(config, secrets, prices)
        risk = RiskManager(config, prices)
        notifier = Notifier(config, secrets)
        log.info(
            "Modus %s, Broker %s, Risiko %.2f Prozent pro Trade",
            config.execution.mode, broker.name, config.risk.risk_per_trade_pct,
        )
        return cls(config, secrets, prices, broker, risk, notifier)
