"""Quellen abfragen und in die Datenbank schreiben.

Jede Quelle hat einen eigenen Cursor. Faellt eine aus, laufen die anderen
weiter, und beim naechsten Durchlauf holt die ausgefallene ab ihrem letzten
Stand nach. Doppelte Eintraege sind unvermeidlich, weil sich die Quellen
ueberschneiden, deshalb der Fingerprint aus Person, Ticker, Richtung und
Handelsdatum.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.exc import IntegrityError

from ..db import get_or_create_actor, get_state, session_scope, set_state
from ..models import Disclosure, Post
from ..settings import AppConfig, Secrets
from ..sources.base import DisclosureSource, PostSource, RawPost, RawTrade
from ..sources.registry import build_enabled_sources
from ..util import clean_symbol, fingerprint, to_utc

log = logging.getLogger(__name__)


def cursor_key(source_name: str) -> str:
    return f"cursor:{source_name}"


def ingest_all(
    config: AppConfig,
    secrets: Secrets,
    *,
    kinds: tuple[str, ...] = ("disclosure", "post"),
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in build_enabled_sources(config, secrets):
        if source.kind not in kinds:
            continue
        try:
            counts[source.name] = ingest_source(source)
        except Exception as exc:  # noqa: BLE001
            log.exception("Quelle %s abgebrochen: %s", source.name, exc)
            counts[source.name] = -1
    return counts


def ingest_source(source: DisclosureSource | PostSource) -> int:
    with session_scope() as session:
        state = get_state(session, cursor_key(source.name), {}) or {}
    since = to_utc(state.get("last_seen")) if state.get("last_seen") else None

    started = dt.datetime.now(dt.UTC)
    new_rows = 0
    newest = since

    for item in source.fetch(since):
        try:
            if isinstance(item, RawTrade):
                stored, stamp = _store_trade(item)
            elif isinstance(item, RawPost):
                stored, stamp = _store_post(item)
            else:
                continue
        except IntegrityError:
            continue
        except Exception as exc:  # noqa: BLE001
            log.debug("Eintrag uebersprungen: %s", exc)
            continue
        new_rows += int(stored)
        if stamp and (newest is None or stamp > newest):
            newest = stamp

    with session_scope() as session:
        set_state(
            session,
            cursor_key(source.name),
            {
                "last_seen": (newest or started).isoformat(),
                "last_run": started.isoformat(),
                "new_rows": new_rows,
            },
        )
    log.info("%s: %d neue Eintraege", source.name, new_rows)
    return new_rows


def _store_trade(trade: RawTrade) -> tuple[bool, dt.datetime | None]:
    symbol = clean_symbol(trade.symbol)
    if not symbol or not trade.transaction_date:
        return False, trade.disclosed_at

    # Bewusst ohne Quelle im Fingerprint: derselbe Trade aus zwei Feeds soll
    # nur einmal in der Datenbank landen.
    fp = fingerprint(
        trade.external_actor_id.lower(),
        symbol,
        trade.side,
        trade.transaction_date.isoformat(),
        round(trade.quantity or 0, 4),
    )
    with session_scope() as session:
        existing = session.query(Disclosure).filter_by(fingerprint=fp).one_or_none()
        if existing:
            return False, trade.disclosed_at
        actor = get_or_create_actor(
            session,
            source=trade.source,
            external_id=trade.external_actor_id,
            name=trade.actor_name,
            actor_type=trade.actor_type,
            party=trade.party,
            chamber=trade.chamber,
            state=trade.state,
        )
        session.add(
            Disclosure(
                fingerprint=fp,
                actor_id=actor.id,
                source=trade.source,
                symbol=symbol,
                asset_class=trade.asset_class,
                side=trade.side,
                transaction_date=trade.transaction_date,
                disclosed_at=trade.disclosed_at,
                amount_low=trade.amount_low,
                amount_high=trade.amount_high,
                price=trade.price,
                quantity=trade.quantity,
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit,
                option_type=trade.option_type,
                raw=trade.raw,
            )
        )
    return True, trade.disclosed_at


def _store_post(post: RawPost) -> tuple[bool, dt.datetime | None]:
    with session_scope() as session:
        existing = (
            session.query(Post)
            .filter_by(platform=post.platform, external_id=post.external_id)
            .one_or_none()
        )
        if existing:
            return False, post.posted_at
        actor = None
        if post.author_id:
            actor = get_or_create_actor(
                session,
                source=post.platform,
                external_id=post.author_id,
                name=post.author,
                actor_type="politician",
                handles={post.platform: post.author},
            )
        session.add(
            Post(
                platform=post.platform,
                external_id=post.external_id,
                actor_id=actor.id if actor else None,
                author=post.author,
                text=post.text,
                url=post.url,
                posted_at=post.posted_at,
            )
        )
    return True, post.posted_at
