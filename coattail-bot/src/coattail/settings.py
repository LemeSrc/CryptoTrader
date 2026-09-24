"""Konfiguration: YAML fuer Strategie-Parameter, ENV fuer alles Geheime.

Geheimnisse landen nie in der YAML. Sie kommen aus .env bzw. aus den
Umgebungsvariablen des Hosts (systemd, Docker, GitHub Actions Secrets).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_default_config() -> Path:
    """Wo config.yaml liegt, haengt von der Installationsart ab.

    Bei einem Checkout mit 'pip install -e .' liegt settings.py unter
    src/coattail/, drei Ebenen ueber der Datei ist dann der Projektordner.
    Bei einer normalen Installation ('pip install .', genau das macht
    deploy/setup_vps.sh) landet dieselbe Datei aber tief in
    .venv/lib/pythonX.Y/site-packages/coattail/, und dieselbe Rechnung
    zeigt ins Leere. Deshalb zuerst die Orte pruefen, an denen die Datei
    im Betrieb tatsaechlich liegt, und den Quellcode-Pfad nur als letzten
    Rueckfall nehmen.
    """
    candidates = [
        Path.cwd() / "config" / "config.yaml",  # systemd WorkingDirectory=/opt/coattail
        Path("/opt/coattail/config/config.yaml"),  # Standardablage von setup_vps.sh
        Path(__file__).resolve().parents[2] / "config" / "config.yaml",  # Checkout aus dem Quellcode
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = _find_default_config()


def _load_env_file() -> None:
    """.env auch in os.environ laden, nicht nur in die Secrets-Klasse.

    Einige Einstellungen werden direkt aus der Umgebung gelesen, allen voran
    COATTAIL_CONTACT fuer den User-Agent. Die SEC sperrt Abrufe ohne echte
    Kontaktadresse. Unter systemd kommt die Datei ueber EnvironmentFile ohnehin
    an, beim Aufruf von Hand aus /opt/coattail aber nur ueber diesen Weg.
    Bereits gesetzte Variablen gewinnen immer.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - kommt mit pydantic-settings
        return
    for candidate in (
        Path(os.getenv("COATTAIL_ENV_FILE", ".env")),
        Path("/opt/coattail/.env"),
    ):
        try:
            if candidate.is_file():
                load_dotenv(candidate, override=False)
                return
        except OSError:  # keine Leserechte, etwa als anderer Benutzer
            continue


_load_env_file()


class Secrets(BaseSettings):
    """Alles, was nicht ins Repo darf."""

    model_config = SettingsConfigDict(
        env_file=os.getenv("COATTAIL_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Datenquellen
    quiver_api_key: str | None = None
    finnhub_api_key: str | None = None
    fmp_api_key: str | None = None
    unusualwhales_api_key: str | None = None
    congress_gov_api_key: str | None = None
    invo_session_cookie: str | None = None
    invo_api_key: str | None = None
    x_bearer_token: str | None = None

    # Ausfuehrung
    alpaca_key_id: str | None = None
    alpaca_secret_key: str | None = None
    exchange_api_key: str | None = None
    exchange_api_secret: str | None = None

    # Anreicherung / Benachrichtigung
    anthropic_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    def has(self, name: str) -> bool:
        return bool(getattr(self, name, None))


class RiskConfig(BaseModel):
    equity_base: float = 10_000.0
    risk_per_trade_pct: float = 0.5
    max_position_pct: float = 5.0
    max_ticker_exposure_pct: float = 8.0
    max_sector_exposure_pct: float = 25.0
    max_open_positions: int = 20
    max_gross_exposure_pct: float = 80.0
    daily_loss_limit_pct: float = 3.0
    max_drawdown_pct: float = 12.0
    default_stop_atr_mult: float = 2.5
    default_take_profit_r: float = 3.0
    min_order_notional: float = 25.0
    require_stop_for_leverage: bool = True

    @field_validator("risk_per_trade_pct")
    @classmethod
    def _sane_risk(cls, v: float) -> float:
        if not 0 < v <= 5:
            raise ValueError("risk_per_trade_pct muss zwischen 0 und 5 Prozent liegen")
        return v


class ScoringConfig(BaseModel):
    min_trades: int = 12
    min_score: float = 55.0
    min_win_rate: float = 0.45
    max_disclosure_lag_days: int = 45
    lookback_days: int = 1095
    horizons_days: list[int] = Field(default_factory=lambda: [21, 63, 126])
    benchmark: str = "SPY"
    prior_win_rate: float = 0.5
    prior_weight: float = 20.0
    recency_halflife_days: float = 365.0
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "win_rate": 0.30,
            "expectancy": 0.25,
            "profit_factor": 0.15,
            "consistency": 0.10,
            "risk_discipline": 0.10,
            "freshness": 0.10,
        }
    )
    rescore_interval_hours: int = 24


class SourceConfig(BaseModel):
    name: str
    enabled: bool = False
    kind: str = ""
    poll_seconds: int = 900
    options: dict[str, Any] = Field(default_factory=dict)


class ExecutionConfig(BaseModel):
    mode: Literal["dry_run", "paper", "live"] = "dry_run"
    broker: Literal["paper", "alpaca", "ccxt"] = "paper"
    exchange_id: str = "binance"
    alpaca_paper: bool = True
    order_type: Literal["market", "limit"] = "market"
    limit_offset_bps: float = 10.0
    max_signal_age_minutes: int = 2880
    max_slippage_bps: float = 40.0
    trading_hours_only: bool = True
    allow_short: bool = False
    allow_options: bool = False
    fractional_shares: bool = True


class NotifyConfig(BaseModel):
    telegram_enabled: bool = False
    notify_on: list[str] = Field(
        default_factory=lambda: ["order", "rejection", "kill_switch", "daily_summary"]
    )
    daily_summary_hour_utc: int = 21


class AppConfig(BaseModel):
    timezone: str = "Europe/Berlin"
    database_url: str = "sqlite:///data/coattail.db"
    log_level: str = "INFO"
    risk: RiskConfig = Field(default_factory=RiskConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    sources: list[SourceConfig] = Field(default_factory=list)
    ticker_blocklist: list[str] = Field(default_factory=list)
    ticker_allowlist: list[str] = Field(default_factory=list)

    def source(self, name: str) -> SourceConfig | None:
        return next((s for s in self.sources if s.name == name), None)

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


def load_config(path: str | Path | None = None) -> AppConfig:
    cfg_path = Path(path or os.getenv("COATTAIL_CONFIG", DEFAULT_CONFIG))
    if not cfg_path.exists():
        return AppConfig()
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_secrets() -> Secrets:
    return Secrets()
