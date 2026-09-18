"""Kommandozeile. Ein Befehl pro Frage, die man an den Bot hat."""

from __future__ import annotations

import logging
import signal as os_signal
import time

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from .app import App
from .db import session_scope
from .models import Actor, ActorStat, Disclosure, Order, Position, Post, Signal
from .pipeline.execute import execute_pending
from .pipeline.ingest import ingest_all, ingest_source
from .pipeline.reconcile import reconcile
from .pipeline.signals import build_signals
from .risk.manager import reset_kill_switch, trip_kill_switch
from .scoring.score import latest_stat
from .settings import get_secrets, load_config
from .sources.registry import build_source
from .tasks import daily_report, rescore_all

app = typer.Typer(add_completion=False, help="Coattail: Copy-Trading nach oeffentlichen Spuren")
console = Console()
log = logging.getLogger("coattail.cli")


@app.command()
def doctor(config: str = typer.Option(None, "--config", "-c")) -> None:
    """Prueft Konfiguration, Zugangsdaten, Quellen, Datenbank und Kurse."""
    cfg = load_config(config)
    secrets = get_secrets()
    ctx = App.create(config)

    table = Table(title="Systemcheck", show_lines=False)
    table.add_column("Bereich")
    table.add_column("Status")
    table.add_column("Anmerkung")

    table.add_row("Modus", cfg.execution.mode, f"Broker {ctx.broker.name}")
    table.add_row(
        "Risiko",
        f"{cfg.risk.risk_per_trade_pct:.2f} Prozent",
        f"Tageslimit {cfg.risk.daily_loss_limit_pct} Prozent, Rueckgang {cfg.risk.max_drawdown_pct} Prozent",
    )

    for sc in cfg.sources:
        if not sc.enabled:
            table.add_row(sc.name, "aus", "in der Konfiguration deaktiviert")
            continue
        src = build_source(cfg, secrets, sc)
        if src is None:
            from .sources.registry import NON_SOURCES

            if sc.kind in NON_SOURCES:
                table.add_row(sc.name, "an", "Schalter, keine Datenquelle")
            else:
                table.add_row(sc.name, "Fehler", "unbekannter Typ")
            continue
        ok, reason = src.available()
        table.add_row(sc.name, "bereit" if ok else "blockiert", reason)

    price = ctx.prices.last_price(cfg.scoring.benchmark)
    table.add_row(
        "Kursdaten",
        "ok" if price else "Fehler",
        f"{cfg.scoring.benchmark} bei {price:.2f}" if price else "kein Kurs abrufbar",
    )
    table.add_row(
        "Benachrichtigung",
        "an" if ctx.notifier.enabled else "aus",
        "Telegram" if ctx.notifier.enabled else "kein Token oder ausgeschaltet",
    )
    table.add_row("KI-Auswertung", "an" if secrets.has("anthropic_api_key") else "aus", "Posts")

    with session_scope() as session:
        counts = {
            "Personen": session.scalar(select(func.count()).select_from(Actor)) or 0,
            "Meldungen": session.scalar(select(func.count()).select_from(Disclosure)) or 0,
            "Beitraege": session.scalar(select(func.count()).select_from(Post)) or 0,
            "Signale": session.scalar(select(func.count()).select_from(Signal)) or 0,
        }
    table.add_row("Datenbank", "ok", ", ".join(f"{k}: {v}" for k, v in counts.items()))
    console.print(table)


@app.command()
def ingest(
    source: str = typer.Option(None, "--source", "-s", help="nur diese eine Quelle"),
    kind: str = typer.Option("all", "--kind", help="disclosure, post oder all"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Holt neue Daten aus den aktiven Quellen."""
    ctx = App.create(config)
    if source:
        sc = ctx.config.source(source)
        if not sc:
            console.print(f"[red]Quelle {source} steht nicht in der Konfiguration[/red]")
            raise typer.Exit(1)
        src = build_source(ctx.config, ctx.secrets, sc)
        ok, reason = src.available() if src else (False, "unbekannter Typ")
        if not ok:
            console.print(f"[red]{source}: {reason}[/red]")
            raise typer.Exit(1)
        console.print(f"{source}: {ingest_source(src)} neue Eintraege")
        return

    kinds = ("disclosure", "post") if kind == "all" else (kind,)
    result = ingest_all(ctx.config, ctx.secrets, kinds=kinds)
    for name, count in result.items():
        console.print(f"{name}: {'Fehler' if count < 0 else f'{count} neu'}")


@app.command()
def score(
    source: str = typer.Option(None, "--source", "-s"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Bewertet alle Personen neu. Laeuft im Dauerbetrieb automatisch nachts."""
    ctx = App.create(config)
    result = rescore_all(ctx, only_source=source)
    console.print(f"{result['bewertet']} bewertet, {result['geeignet']} freigegeben")


@app.command()
def actors(
    top: int = typer.Option(25, "--top", "-n"),
    eligible_only: bool = typer.Option(False, "--eligible"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Rangliste der beobachteten Personen."""
    App.create(config)
    table = Table(title="Bewertung der Vorbilder")
    for col in ("Name", "Typ", "Quelle", "Note", "Treffer", "Trades", "Verzug", "Erwartung", "Status"):
        table.add_column(col)

    with session_scope() as session:
        newest = (
            select(ActorStat.actor_id, func.max(ActorStat.computed_at).label("ts"))
            .group_by(ActorStat.actor_id)
            .subquery()
        )
        stmt = (
            select(ActorStat, Actor)
            .join(newest, (ActorStat.actor_id == newest.c.actor_id) & (ActorStat.computed_at == newest.c.ts))
            .join(Actor, Actor.id == ActorStat.actor_id)
            .order_by(ActorStat.score.desc())
            .limit(top)
        )
        if eligible_only:
            stmt = stmt.where(ActorStat.eligible.is_(True))
        for stat, actor in session.execute(stmt):
            table.add_row(
                actor.name[:28],
                actor.actor_type,
                actor.source,
                f"{stat.score:.1f}",
                f"{stat.win_rate_shrunk:.0%}",
                str(stat.n_closed),
                f"{stat.median_lag_days:.0f}d",
                f"{stat.expectancy:+.2%}",
                "[green]kopieren[/green]" if stat.eligible else f"[dim]{stat.reasons[0][:34]}[/dim]",
            )
    console.print(table)


@app.command()
def why(name: str, config: str = typer.Option(None, "--config", "-c")) -> None:
    """Begruendung fuer eine einzelne Person im Detail."""
    App.create(config)
    with session_scope() as session:
        actor = session.scalar(select(Actor).where(Actor.name.ilike(f"%{name}%")))
        if not actor:
            console.print(f"[red]Keine Person zu '{name}' gefunden[/red]")
            raise typer.Exit(1)
        stat = latest_stat(session, actor.id)
        if not stat:
            console.print(f"{actor.name}: noch nicht bewertet, 'coattail score' ausfuehren")
            raise typer.Exit(0)
        console.print(f"[bold]{actor.name}[/bold] ({actor.actor_type}, {actor.source})")
        console.print(f"Note {stat.score:.1f} | geeignet: {'ja' if stat.eligible else 'nein'}")
        console.print(
            f"Trefferquote roh {stat.win_rate:.0%}, geschrumpft {stat.win_rate_shrunk:.0%} "
            f"aus {stat.n_closed} auswertbaren von {stat.n_trades} Trades"
        )
        console.print(
            f"Erwartung {stat.expectancy:+.2%} | Gewinnfaktor {stat.profit_factor:.2f} | "
            f"groesster Rueckgang {stat.max_drawdown:.0%}"
        )
        console.print(
            f"Alpha ab Handelstag {stat.alpha_from_trade:+.2%}, ab Meldetag "
            f"{stat.alpha_from_disclosure:+.2%} (nur der zweite Wert ist kopierbar)"
        )
        console.print(f"Meldeverzug im Mittel {stat.median_lag_days:.0f} Tage")
        console.print(f"Stops gesetzt bei {stat.stop_usage_rate:.0%} der Trades")
        console.print("Begruendung: " + "; ".join(stat.reasons))
        if stat.detail:
            console.print(f"[dim]{stat.detail}[/dim]")


@app.command("set-override")
def set_override(
    name: str,
    mode: str = typer.Argument(..., help="allow, block oder clear"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Person von Hand freigeben oder sperren, unabhaengig von der Note."""
    App.create(config)
    if mode not in ("allow", "block", "clear"):
        console.print("[red]mode muss allow, block oder clear sein[/red]")
        raise typer.Exit(1)
    with session_scope() as session:
        actor = session.scalar(select(Actor).where(Actor.name.ilike(f"%{name}%")))
        if not actor:
            console.print(f"[red]Keine Person zu '{name}' gefunden[/red]")
            raise typer.Exit(1)
        actor.manual_override = None if mode == "clear" else mode
        console.print(f"{actor.name}: {mode}")


@app.command()
def signals(
    status: str = typer.Option(None, "--status"),
    limit: int = typer.Option(30, "--limit", "-n"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Die zuletzt erzeugten Signale mit Status und Ablehnungsgrund."""
    App.create(config)
    table = Table(title="Signale")
    for col in ("Zeit", "Titel", "Seite", "Herkunft", "Vorbild", "Note", "Status", "Anmerkung"):
        table.add_column(col)
    with session_scope() as session:
        stmt = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(Signal.status == status)
        for s in session.scalars(stmt):
            table.add_row(
                s.event_at.strftime("%d.%m %H:%M"),
                s.symbol,
                s.side,
                s.origin,
                str(s.payload.get("actor") or s.payload.get("author") or "")[:22],
                f"{s.actor_score:.0f}",
                s.status,
                (s.reject_reason or "")[:38],
            )
    console.print(table)


@app.command()
def positions(config: str = typer.Option(None, "--config", "-c")) -> None:
    """Offene Positionen und Kapitalstand."""
    ctx = App.create(config)
    table = Table(title=f"Positionen ({ctx.broker.name})")
    for col in ("Titel", "Menge", "Einstand", "Aktuell", "Ergebnis", "Stop", "Vorbilder"):
        table.add_column(col)
    with session_scope() as session:
        rows = list(
            session.scalars(select(Position).where(Position.closed_at.is_(None)))
        )
        for p in rows:
            last = ctx.prices.last_price(p.symbol) or p.avg_price
            pnl = (last - p.avg_price) * p.quantity
            table.add_row(
                p.symbol,
                f"{p.quantity:.4f}",
                f"{p.avg_price:.2f}",
                f"{last:.2f}",
                f"{pnl:+.2f}",
                f"{p.stop_loss:.2f}" if p.stop_loss else "-",
                ", ".join(p.source_actors or [])[:26],
            )
    console.print(table)
    console.print(f"Kapital: {ctx.broker.equity():,.2f}")


@app.command()
def orders(
    limit: int = typer.Option(25, "--limit", "-n"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Auftragsbuch."""
    App.create(config)
    table = Table(title="Auftraege")
    for col in ("Zeit", "Titel", "Seite", "Menge", "Kurs", "Status", "Risiko"):
        table.add_column(col)
    with session_scope() as session:
        for o in session.scalars(select(Order).order_by(Order.created_at.desc()).limit(limit)):
            table.add_row(
                o.created_at.strftime("%d.%m %H:%M"),
                o.symbol,
                o.side,
                f"{o.quantity:.4f}",
                f"{o.filled_price:.2f}" if o.filled_price else "-",
                o.status,
                f"{o.risk_amount:.2f}" if o.risk_amount else "-",
            )
    console.print(table)


@app.command("run-once")
def run_once(config: str = typer.Option(None, "--config", "-c")) -> None:
    """Ein vollstaendiger Durchlauf: holen, bewerten, signalisieren, handeln."""
    ctx = App.create(config)
    console.print("Daten holen ...")
    ingest_all(ctx.config, ctx.secrets)
    console.print("Signale bilden ...")
    created = build_signals(ctx.config, ctx.secrets)
    console.print(f"{created} Signale")
    result = execute_pending(ctx.config, ctx.broker, ctx.risk, ctx.notifier)
    console.print(
        f"Ausgefuehrt {result['executed']}, abgelehnt {result['rejected']}, "
        f"uebersprungen {result['skipped']}"
    )
    reconcile(ctx.config, ctx.broker, ctx.prices, ctx.risk, ctx.notifier)


@app.command()
def run(config: str = typer.Option(None, "--config", "-c")) -> None:
    """Dauerbetrieb. Genau das, was unter systemd oder im Container laeuft."""
    ctx = App.create(config)
    from .scheduler import build_scheduler

    sched = build_scheduler(ctx)
    sched.start()
    console.print("[green]Coattail laeuft.[/green] Beenden mit Strg+C.")
    for job in sched.get_jobs():
        console.print(f"  {job.id}: naechster Lauf {job.next_run_time}")

    stopping = {"flag": False}

    def _stop(signum, frame):  # noqa: ANN001, ARG001
        stopping["flag"] = True

    os_signal.signal(os_signal.SIGINT, _stop)
    os_signal.signal(os_signal.SIGTERM, _stop)
    try:
        while not stopping["flag"]:
            time.sleep(1)
    finally:
        sched.shutdown(wait=False)
        console.print("Beendet.")


@app.command()
def kill(
    reason: str = typer.Argument("von Hand ausgeloest"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Notaus. Es werden keine neuen Positionen mehr eroeffnet."""
    ctx = App.create(config)
    with session_scope() as session:
        trip_kill_switch(session, reason)
    ctx.notifier.kill_switch(reason)
    console.print(f"[red]Notaus aktiv:[/red] {reason}")


@app.command()
def resume(config: str = typer.Option(None, "--config", "-c")) -> None:
    """Notaus zuruecksetzen."""
    App.create(config)
    with session_scope() as session:
        reset_kill_switch(session)
    console.print("[green]Handel wieder freigegeben[/green]")


@app.command()
def report(config: str = typer.Option(None, "--config", "-c")) -> None:
    """Tagesbericht sofort erzeugen."""
    ctx = App.create(config)
    for line in daily_report(ctx):
        console.print(line)


@app.command()
def backtest(
    days: int = typer.Option(365, "--days"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Rueckrechnung: was haette das Kopieren der freigegebenen Personen gebracht."""
    ctx = App.create(config)
    from .scoring.backtest import run_backtest

    result = run_backtest(ctx, days=days)
    table = Table(title=f"Rueckrechnung ueber {days} Tage")
    for col in ("Kennzahl", "Wert"):
        table.add_column(col)
    for key, value in result.items():
        table.add_row(key, str(value))
    console.print(table)
    console.print(
        "[dim]Die Rechnung nimmt den Meldetag als Einstieg, nicht den Handelstag. "
        "Alles andere waere nicht nachhandelbar.[/dim]"
    )


@app.command()
def bootstrap(
    days: int = typer.Option(1095, "--days", help="Wie weit die Historie geladen wird"),
    config: str = typer.Option(None, "--config", "-c"),
) -> None:
    """Erstbefuellung: Historie holen, Personen bewerten, Ergebnis zeigen."""
    ctx = App.create(config)
    console.print(f"Historie der letzten {days} Tage holen ...")
    ingest_all(ctx.config, ctx.secrets, kinds=("disclosure",))
    console.print("Personen bewerten, das dauert beim ersten Mal ...")
    result = rescore_all(ctx)
    console.print(f"{result['bewertet']} bewertet, {result['geeignet']} freigegeben")
    console.print("Naechster Schritt: coattail actors --eligible")


@app.command()
def version() -> None:
    from . import __version__

    console.print(f"coattail {__version__}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
