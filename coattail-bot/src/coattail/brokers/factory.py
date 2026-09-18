from __future__ import annotations

import logging

from ..prices import PriceProvider
from ..settings import AppConfig, Secrets
from .base import Broker
from .paper import PaperBroker

log = logging.getLogger(__name__)


def build_broker(config: AppConfig, secrets: Secrets, prices: PriceProvider) -> Broker:
    """Im Trockenlauf und im Papiermodus immer der Simulationsbroker.
    Echtes Geld gibt es nur bei mode: live plus passenden Zugangsdaten."""
    exec_cfg = config.execution
    if exec_cfg.mode in ("dry_run", "paper") and exec_cfg.broker == "paper":
        return PaperBroker(prices, starting_equity=config.risk.equity_base)

    if exec_cfg.broker == "alpaca":
        if not (secrets.alpaca_key_id and secrets.alpaca_secret_key):
            log.error("Alpaca gewaehlt, aber keine Zugangsdaten. Fallback auf Papierhandel.")
            return PaperBroker(prices, starting_equity=config.risk.equity_base)
        from .alpaca import AlpacaBroker

        return AlpacaBroker(
            secrets.alpaca_key_id,
            secrets.alpaca_secret_key,
            paper=exec_cfg.alpaca_paper or exec_cfg.mode != "live",
        )

    if exec_cfg.broker == "ccxt":
        if not (secrets.exchange_api_key and secrets.exchange_api_secret):
            log.error("Boerse gewaehlt, aber keine Zugangsdaten. Fallback auf Papierhandel.")
            return PaperBroker(prices, starting_equity=config.risk.equity_base)
        from .ccxt_broker import CcxtBroker

        return CcxtBroker(
            exec_cfg.exchange_id,
            secrets.exchange_api_key,
            secrets.exchange_api_secret,
            sandbox=exec_cfg.mode != "live",
        )

    return PaperBroker(prices, starting_equity=config.risk.equity_base)
