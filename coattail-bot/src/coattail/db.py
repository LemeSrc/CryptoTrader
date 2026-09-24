"""Engine, Session und ein paar Helfer, die ueberall gebraucht werden."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Actor, Base, RunState
from .settings import AppConfig

_engine = None
_Session: sessionmaker[Session] | None = None


def init_engine(config: AppConfig):
    global _engine, _Session
    url = config.database_url
    if url.startswith("sqlite:///"):
        Path(url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)
    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite"):
        # Mehrere Quellen schreiben parallel. Ohne Wartezeit bricht der
        # zweite Schreiber sofort mit 'database is locked' ab.
        connect_args = {"timeout": 30, "check_same_thread": False}
    _engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        event.listen(_engine, "connect", _sqlite_pragmas)
    Base.metadata.create_all(_engine)
    _add_missing_columns(_engine)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    """WAL erlaubt Lesen waehrend eines Schreibvorgangs, und der Dienst liest
    und schreibt staendig gleichzeitig."""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
    finally:
        cur.close()


# Spalten, die nach der ersten Version dazugekommen sind. create_all legt nur
# fehlende Tabellen an, keine fehlenden Spalten in bestehenden.
_LATE_COLUMNS = {
    "positions": {"asset_class": "VARCHAR(16) DEFAULT 'equity'"},
}


def _add_missing_columns(engine) -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _LATE_COLUMNS.items():
            if not insp.has_table(table):
                continue
            present = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


@contextmanager
def session_scope() -> Iterator[Session]:
    if _Session is None:
        raise RuntimeError("init_engine() wurde nicht aufgerufen")
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_or_create_actor(
    session: Session,
    *,
    source: str,
    external_id: str,
    name: str,
    actor_type: str = "politician",
    **extra: Any,
) -> Actor:
    stmt = select(Actor).where(Actor.source == source, Actor.external_id == external_id)
    actor = session.scalar(stmt)
    if actor:
        changed = False
        for key, value in extra.items():
            if value and getattr(actor, key, None) != value:
                setattr(actor, key, value)
                changed = True
        if changed:
            session.flush()
        return actor
    actor = Actor(
        source=source,
        external_id=external_id,
        name=name,
        actor_type=actor_type,
        **{k: v for k, v in extra.items() if v is not None},
    )
    session.add(actor)
    session.flush()
    return actor


def get_state(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(RunState, key)
    return row.value if row else default


def set_state(session: Session, key: str, value: dict[str, Any]) -> None:
    row = session.get(RunState, key)
    if row:
        row.value = value
        row.updated_at = dt.datetime.now(dt.UTC)
    else:
        session.add(RunState(key=key, value=value))
    session.flush()
