"""Aus Rohdaten werden Signale, aber nur von Personen, die die Pruefung bestehen.

Hier greift die Vorauswahl: gemeldet wird alles, kopiert wird wenig. Ein
Eintrag wird nur dann zum Signal, wenn die Person zuletzt als geeignet
bewertet wurde, das Ereignis frisch genug ist und der Titel handelbar ist.

Die Ueberzeugung ergibt sich aus drei Dingen: wie gut die Person bewertet ist,
wie gross der Einsatz im Verhaeltnis zu ihren sonstigen Trades war, und wie
schnell die Meldung kam. Wer fuer seine Verhaeltnisse gross einsteigt und
schnell meldet, bekommt mehr Gewicht als jemand mit einer Pflichtmeldung im
Mindestbetrag nach sechs Wochen.
"""

from __future__ import annotations

import datetime as dt
import logging
import statistics

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Actor, ActorStat, Disclosure, Post, Signal
from ..nlp.classifier import HybridClassifier
from ..scoring.score import latest_stat
from ..settings import AppConfig, Secrets
from ..util import clamp, fingerprint
from .ingest import cursor_key

log = logging.getLogger(__name__)

SIGNAL_CURSOR = "signals"


def build_signals(config: AppConfig, secrets: Secrets) -> int:
    from ..db import session_scope, set_state

    created = 0
    with session_scope() as session:
        created += _from_disclosures(session, config)
        created += _from_posts(session, config, secrets)
        set_state(
            session,
            cursor_key(SIGNAL_CURSOR),
            {"last_run": dt.datetime.now(dt.UTC).isoformat(), "created": created},
        )
    log.info("%d neue Signale erzeugt", created)
    return created


def _from_disclosures(session: Session, config: AppConfig) -> int:
    """Alle noch frischen Meldungen pruefen, nicht nur die seit dem letzten Lauf.

    Frueher lief hier ein Zeiger ueber die Meldungsnummer. Der schob sich auch
    ueber Meldungen hinweg, deren Person zu dem Zeitpunkt noch gar nicht
    bewertet war, etwa direkt nach dem Start oder vor der naechtlichen
    Bewertung. Wurde die Person danach freigegeben, war die Meldung verloren.
    Jetzt wird jeder Lauf das ganze Frischefenster durchgesehen, doppelte
    Signale verhindert der Schluessel.
    """
    max_age = dt.timedelta(minutes=config.execution.max_signal_age_minutes)
    now = dt.datetime.now(dt.UTC)
    cutoff = now - max_age
    rows = list(
        session.scalars(
            select(Disclosure)
            .where(
                Disclosure.ingested_at >= cutoff,
                (Disclosure.disclosed_at >= cutoff) | Disclosure.disclosed_at.is_(None),
            )
            .order_by(Disclosure.id.asc())
        )
    )
    if not rows:
        return 0

    created = 0
    stats: dict[int, ActorStat | None] = {}

    for d in rows:
        key = fingerprint("disc", d.fingerprint)
        if session.scalar(select(Signal.id).where(Signal.dedupe_key == key)):
            continue
        actor = session.get(Actor, d.actor_id)
        if actor is None:
            continue
        if actor.id not in stats:
            stats[actor.id] = latest_stat(session, actor.id)
        stat = stats[actor.id]
        if stat is None or not stat.eligible:
            continue

        event_at = d.disclosed_at or dt.datetime.combine(
            d.transaction_date, dt.time(21, 0), tzinfo=dt.UTC
        )
        if now - event_at > max_age:
            continue
        if d.option_type and not config.execution.allow_options:
            continue

        conviction = _conviction(session, actor, d, stat.score)

        session.add(
            Signal(
                dedupe_key=key,
                actor_id=actor.id,
                origin=d.source,
                origin_ref=str(d.id),
                symbol=d.symbol,
                asset_class=d.asset_class,
                side=d.side,
                intent="close" if d.side == "sell" else "open",
                event_at=event_at,
                reference_price=d.price,
                stop_loss=d.stop_loss,
                take_profit=d.take_profit,
                size_hint_pct=None,
                conviction=conviction,
                actor_score=stat.score,
                payload={
                    "actor": actor.name,
                    "actor_type": actor.actor_type,
                    "win_rate": stat.win_rate_shrunk,
                    "lag_days": d.lag_days,
                    "amount": [d.amount_low, d.amount_high],
                },
            )
        )
        created += 1
    return created


def _from_posts(session: Session, config: AppConfig, secrets: Secrets) -> int:
    source_cfg = config.source("posts_to_signals")
    if source_cfg is None or not source_cfg.enabled:
        return 0

    classifier = HybridClassifier(
        secrets,
        model=source_cfg.options.get("model", "claude-sonnet-5"),
        use_llm=bool(source_cfg.options.get("use_llm", True)),
    )
    min_conviction = float(source_cfg.options.get("min_conviction", 0.55))
    authors = {a.lower() for a in source_cfg.options.get("authors", [])}
    max_age = dt.timedelta(minutes=int(source_cfg.options.get("max_age_minutes", 180)))
    now = dt.datetime.now(dt.UTC)
    created = 0

    rows = list(session.scalars(select(Post).where(Post.analyzed.is_(False)).limit(200)))
    for post in rows:
        post.analyzed = True
        posted = post.posted_at if post.posted_at.tzinfo else post.posted_at.replace(tzinfo=dt.UTC)
        if now - posted > max_age:
            continue
        if authors and post.author.lower() not in authors:
            continue

        results = classifier.classify(post.text, post.author)
        post.analysis = {
            "signals": [
                {"symbol": r.symbol, "side": r.side, "conviction": r.conviction, "why": r.rationale}
                for r in results
            ]
        }
        for res in results:
            if res.conviction < min_conviction:
                continue
            key = fingerprint("post", post.platform, post.external_id, res.symbol, res.side)
            if session.scalar(select(Signal).where(Signal.dedupe_key == key)):
                continue
            if _recent_post_signal(session, res.symbol, res.side, posted):
                # Derselbe Beitrag kommt oft ueber zwei Wege an, etwa Truth
                # Social direkt und ueber das RSS-Archiv. Einmal reicht.
                continue
            session.add(
                Signal(
                    dedupe_key=key,
                    actor_id=post.actor_id,
                    origin=f"post:{post.platform}",
                    origin_ref=post.external_id,
                    symbol=res.symbol,
                    asset_class=res.asset_class,
                    side=res.side,
                    intent="open",
                    event_at=posted,
                    conviction=res.conviction,
                    actor_score=float(source_cfg.options.get("post_actor_score", 60.0)),
                    payload={"author": post.author, "why": res.rationale, "url": post.url},
                )
            )
            created += 1
    return created


def _recent_post_signal(
    session: Session, symbol: str, side: str, posted: dt.datetime, window_minutes: int = 60
) -> bool:
    window = dt.timedelta(minutes=window_minutes)
    return (
        session.scalar(
            select(Signal.id)
            .where(
                Signal.origin.like("post:%"),
                Signal.symbol == symbol,
                Signal.side == side,
                Signal.event_at >= posted - window,
                Signal.event_at <= posted + window,
            )
            .limit(1)
        )
        is not None
    )


def _conviction(session: Session, actor: Actor, d: Disclosure, score: float) -> float:
    """0 bis 1. Groesse im Verhaeltnis zur eigenen Historie und Meldetempo."""
    base = clamp((score - 50.0) / 50.0, 0.0, 1.0) * 0.5 + 0.25

    amounts = [
        (row.amount_high or row.amount_low or 0.0)
        for row in session.scalars(
            select(Disclosure).where(Disclosure.actor_id == actor.id).limit(300)
        )
        if (row.amount_high or row.amount_low)
    ]
    this_amount = d.amount_high or d.amount_low or 0.0
    if amounts and this_amount:
        median = statistics.median(amounts)
        if median > 0:
            base += clamp((this_amount / median - 1.0) * 0.15, -0.15, 0.25)

    lag = d.lag_days
    if lag is not None:
        base += 0.15 if lag <= 7 else (0.05 if lag <= 21 else -0.10)

    return round(clamp(base, 0.05, 1.0), 3)
