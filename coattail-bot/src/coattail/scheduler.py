"""Der Taktgeber des Dauerbetriebs.

Unterschiedliche Daten altern unterschiedlich schnell, deshalb unterschiedliche
Intervalle:

  Posts        jede Minute. Der Vorsprung gegenueber der Nachrichtenlage ist
               die einzige Rechtfertigung, sie ueberhaupt auszuwerten.
  Meldungen    alle 15 Minuten. Sie erscheinen in Schueben, schneller bringt
               nichts ausser Last auf fremden Servern.
  Abgleich     alle 5 Minuten. Stops sollen nicht bis zum naechsten Abruf warten.
  Bewertung    einmal taeglich nachts. Die Kennzahlen aendern sich nicht
               stuendlich, und der Lauf zieht viele Kursreihen.

misfire_grace_time sorgt dafuer, dass nach einem Neustart nicht alle
verpassten Laeufe auf einmal nachgeholt werden.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .app import App
from .pipeline.execute import execute_pending
from .pipeline.ingest import ingest_all
from .pipeline.reconcile import reconcile
from .pipeline.signals import build_signals
from .tasks import daily_report, rescore_all

log = logging.getLogger(__name__)


def build_scheduler(app: App) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=app.config.timezone)
    cfg = app.config

    post_interval = min(
        (s.poll_seconds for s in cfg.enabled_sources() if s.kind in ("bluesky", "x", "mastodon", "rss")),
        default=300,
    )
    disc_interval = min(
        (
            s.poll_seconds
            for s in cfg.enabled_sources()
            if s.kind not in ("bluesky", "x", "mastodon", "rss")
        ),
        default=900,
    )

    sched.add_job(
        lambda: ingest_all(cfg, app.secrets, kinds=("post",)),
        IntervalTrigger(seconds=post_interval),
        id="ingest_posts",
        max_instances=1,
        misfire_grace_time=60,
    )
    sched.add_job(
        lambda: ingest_all(cfg, app.secrets, kinds=("disclosure",)),
        IntervalTrigger(seconds=disc_interval),
        id="ingest_disclosures",
        max_instances=1,
        misfire_grace_time=300,
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
    sched.add_job(
        lambda: daily_report(app),
        CronTrigger(hour=cfg.notify.daily_summary_hour_utc, minute=5),
        id="daily_report",
        max_instances=1,
        misfire_grace_time=3600,
    )
    return sched
