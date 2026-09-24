"""Datenmodell. SQLite reicht bis in den sechsstelligen Signalbereich,
bei Bedarf zeigt DATABASE_URL einfach auf Postgres.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class UtcDateTime(TypeDecorator):
    """Zeitstempel kommen immer mit Zeitzone zurueck.

    SQLite speichert keine Zeitzone und liefert naive Werte. Ohne diese Schicht
    knallt jeder Vergleich mit datetime.now(UTC) irgendwann mitten im Betrieb,
    und zwar genau dann, wenn ein Signal bewertet werden soll.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect) -> dt.datetime | None:  # noqa: ANN001
        if value is None:
            return None
        return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)

    def process_result_value(self, value: dt.datetime | None, dialect) -> dt.datetime | None:  # noqa: ANN001
        if value is None:
            return None
        return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


class Base(DeclarativeBase):
    pass


class Actor(Base):
    """Eine Person, der wir folgen koennten: Abgeordneter, Senator, Insider, Trader."""

    __tablename__ = "actors"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_actor_source_ext"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(48), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    actor_type: Mapped[str] = mapped_column(String(24), default="politician")
    party: Mapped[str | None] = mapped_column(String(32))
    chamber: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(32))
    committees: Mapped[list[str]] = mapped_column(JSON, default=list)
    handles: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    manual_override: Mapped[str | None] = mapped_column(String(16))  # allow | block
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), default=utcnow)

    stats: Mapped[list[ActorStat]] = relationship(back_populates="actor")

    @property
    def key(self) -> str:
        return f"{self.source}:{self.external_id}"


class ActorStat(Base):
    """Ergebnis der Bewertung. Historie bleibt erhalten, damit man sieht,
    wie sich die Einschaetzung einer Person ueber die Zeit bewegt."""

    __tablename__ = "actor_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("actors.id"), index=True)
    computed_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), default=utcnow)
    window_days: Mapped[int] = mapped_column(Integer, default=1095)

    n_trades: Mapped[int] = mapped_column(Integer, default=0)
    n_closed: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate_shrunk: Mapped[float] = mapped_column(Float, default=0.0)
    avg_win: Mapped[float] = mapped_column(Float, default=0.0)
    avg_loss: Mapped[float] = mapped_column(Float, default=0.0)
    profit_factor: Mapped[float] = mapped_column(Float, default=0.0)
    expectancy: Mapped[float] = mapped_column(Float, default=0.0)
    alpha_from_trade: Mapped[float] = mapped_column(Float, default=0.0)
    alpha_from_disclosure: Mapped[float] = mapped_column(Float, default=0.0)
    median_hold_days: Mapped[float] = mapped_column(Float, default=0.0)
    median_lag_days: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    stop_usage_rate: Mapped[float] = mapped_column(Float, default=0.0)
    avg_risk_pct: Mapped[float] = mapped_column(Float, default=0.0)
    consistency: Mapped[float] = mapped_column(Float, default=0.0)
    freshness: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    actor: Mapped[Actor] = relationship(back_populates="stats")


class Disclosure(Base):
    """Rohes Handelsereignis einer Person, so wie die Quelle es meldet."""

    __tablename__ = "disclosures"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_disclosure_fingerprint"),
        Index("ix_disclosure_actor_date", "actor_id", "transaction_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("actors.id"), index=True)
    source: Mapped[str] = mapped_column(String(48), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_class: Mapped[str] = mapped_column(String(16), default="equity")
    side: Mapped[str] = mapped_column(String(8))  # buy | sell
    transaction_date: Mapped[dt.date] = mapped_column(index=True)
    disclosed_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    amount_low: Mapped[float | None] = mapped_column(Float)
    amount_high: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    quantity: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float)
    option_type: Mapped[str | None] = mapped_column(String(16))
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ingested_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), default=utcnow)

    @property
    def lag_days(self) -> float | None:
        if not self.disclosed_at or not self.transaction_date:
            return None
        return (self.disclosed_at.date() - self.transaction_date).days


class Post(Base):
    """Oeffentlicher Beitrag einer beobachteten Person."""

    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_post_platform_ext"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(128))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("actors.id"), index=True)
    author: Mapped[str] = mapped_column(String(160))
    text: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(512))
    posted_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), index=True)
    ingested_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), default=utcnow)
    analyzed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Signal(Base):
    """Normalisierte Handelsabsicht. Alles, was der Bot ausfuehrt, kommt hierher."""

    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_signal_dedupe"),
        Index("ix_signal_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("actors.id"), index=True)
    origin: Mapped[str] = mapped_column(String(48))  # quiver, invo, bluesky, ...
    origin_ref: Mapped[str | None] = mapped_column(String(128))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_class: Mapped[str] = mapped_column(String(16), default="equity")
    side: Mapped[str] = mapped_column(String(8))
    intent: Mapped[str] = mapped_column(String(16), default="open")  # open | close
    event_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), index=True)
    reference_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float)
    size_hint_pct: Mapped[float | None] = mapped_column(Float)
    conviction: Mapped[float] = mapped_column(Float, default=0.5)
    actor_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    reject_reason: Mapped[str | None] = mapped_column(String(256))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), default=utcnow)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("client_order_id", name="uq_order_client_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), index=True)
    broker: Mapped[str] = mapped_column(String(24))
    broker_order_id: Mapped[str | None] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Float)
    order_type: Mapped[str] = mapped_column(String(16), default="market")
    limit_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float)
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    filled_price: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="submitted", index=True)
    risk_amount: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), default=utcnow, onupdate=utcnow
    )


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("symbol", "broker", name="uq_position_symbol_broker"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    broker: Mapped[str] = mapped_column(String(24))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float)
    opened_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), default=utcnow)
    closed_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime())
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    source_actors: Mapped[list[str]] = mapped_column(JSON, default=list)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    taken_at: Mapped[dt.datetime] = mapped_column(UtcDateTime(), default=utcnow, index=True)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    gross_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    day_pnl: Mapped[float] = mapped_column(Float, default=0.0)


class RunState(Base):
    """Key-Value fuer Cursor, Kill-Switch und Laufzeit-Flags."""

    __tablename__ = "run_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime(), default=utcnow, onupdate=utcnow
    )
