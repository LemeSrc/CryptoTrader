"""Engine, Session und ein paar Helfer, die ueberall gebraucht werden."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
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
    _engine = create_engine(url, future=True, pool_pre_ping=True)
    Base.metadata.create_all(_engine)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


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
