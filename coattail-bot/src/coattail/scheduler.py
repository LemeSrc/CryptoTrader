"""Der Taktgeber des Dauerbetriebs.

Unterschiedliche Daten altern unterschiedlich schnell, deshalb hat jede
Quelle ihr eigenes Intervall (poll_seconds in der Konfiguration):

  Posts        ein bis fuenf Minuten. Der Vorsprung gegenueber der
               Nachrichtenlage ist die einzige Rechtfertigung, sie ueberhaupt
               auszuwerten.
  Meldungen    zehn Minuten bis sechs Stunden. Sie erscheinen in Schueben,
               schneller bringt nichts ausser Last auf fremden Servern.
  Abgleich     alle 5 Minuten. Stops sollen nicht bis zum naechsten Abruf warten.
  Bewertung    einmal taeglich nachts. Die Kennzahlen aendern sich nicht
               stuendlich, und der Lauf zieht viele Kursreihen.

misfire_grace_time sorgt dafuer, dass nach einem Neustart nicht alle
verpassten Laeufe auf einmal nachgeholt werden.
"""

from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .app import App
from .pipeline.execute import execute_pending
from .pipeline.ingest import ingest_source
from .pipeline.reconcile import reconcile
from .pipeline.signals import build_signals
from .sources.registry import build_enabled_sources
from .tasks import daily_report, rescore_all

log = logging.getLogger(__name__)


def build_scheduler(app: App) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=app.config.timezone)
    cfg = app.config

    # Jede Quelle bekommt ihren eigenen Takt aus poll_seconds. Frueher liefen
    # alle Meldungsquellen im Takt der schnellsten mit, dann holte etwa
    # usaspending alle zehn Minuten dieselben Grossauftraege ab, obwohl sechs
    # Stunden eingestellt waren. Unhoeflich gegenueber fremden Servern und
    # bei bezahlten Schnittstellen wie X schlicht teuer.
    sources = build_enabled_sources(cfg, app.secrets)
    for index, source in enumerate(sources):
        seconds = max(30, int(source.source_config.poll_seconds))
        sched.add_job(
            ingest_source,
            IntervalTrigger(seconds=seconds, jitter=min(30, seconds // 10)),
            args=[source],
            id=f"ingest:{source.name}",
            max_instances=1,
            misfire_grace_time=max(60, seconds // 2),
            # Erster Lauf kurz nach dem Start, gestaffelt statt alle auf einmal.
            next_run_time=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=10 + index * 5),
        )
        log.info("Quelle %s alle %d Sekunden", source.name, seconds)

    post_interval = min(
        (int(s.source_config.poll_seconds) for s in sources if s.kind == "post"),
        default=300,
    )

    sched.add_job(
        lambda: build_signals(cfg, app.secrets),
        IntervalTrigger(seconds=max(60, post_interval)),
        id="build_signals",
        max_instances=1,
        misfire_grace_time=60,
    )
    sched.add_job(
        lambda: execute_pending(cfg, app.broker, app.risk, app.notifier),
        IntervalTrigger(seconds=max(60, post_interval)),
        id="execute",
        max_instances=1,
        misfire_grace_time=60,
    )
    sched.add_job(
        lambda: reconcile(cfg, app.broker, app.prices, app.risk, app.notifier),
        IntervalTrigger(minutes=5),
        id="reconcile",
        max_instances=1,
        misfire_grace_time=120,
    )
    sched.add_job(
        lambda: rescore_all(app),
        CronTrigger(hour=3, minute=20),
        id="rescore",
        max_instances=1,
        misfire_grace_time=3600,
    )
    if not _scores_fresh():
        # Frische Datenbank oder lange Pause: nicht bis zur Nacht warten. Die
        # Verzoegerung laesst den Quellen Zeit, die Historie erst zu laden.
        sched.add_job(
            lambda: rescore_all(app),
            "date",
            run_date=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=20),
            id="rescore_initial",
            misfire_grace_time=3600,
        )
        log.info("Keine aktuelle Bewertung vorhanden, erster Lauf in 20 Minuten")
    sched.add_job(
        lambda: daily_report(app),
        CronTrigger(hour=cfg.notify.daily_summary_hour_utc, minute=5, timezone="UTC"),
        id="daily_report",
        max_instances=1,
        misfire_grace_time=3600,
    )
    return sched


def _scores_fresh(max_age_hours: float = 36.0) -> bool:
    from sqlalchemy import func, select

    from .db import session_scope
    from .models import ActorStat

    with session_scope() as session:
        newest = session.scalar(select(func.max(ActorStat.computed_at)))
    if newest is None:
        return False
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=dt.UTC)
    return dt.datetime.now(dt.UTC) - newest < dt.timedelta(hours=max_age_hours)
