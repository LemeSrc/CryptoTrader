"""Bewertung einzelner Personen aus ihrer Handelshistorie.

Zwei Faelle, die sich grundlegend unterscheiden:

Politiker melden nur, dass sie gekauft oder verkauft haben, nie warum und
selten mit Gegenposition. Ihre Qualitaet muss aus dem Kursverlauf nach dem
Ereignis rekonstruiert werden.

Trader auf Ausfuehrungsplattformen liefern echte Fills. Dort lassen sich
Rundlaeufe, Trefferquote, Risiko pro Trade und Stopdisziplin direkt rechnen.

Der wichtigste Unterschied im Ergebnis ist die Meldeverzoegerung. Ein Politiker
kann brillant handeln und fuer einen Nachahmer trotzdem wertlos sein, weil die
Meldung erst 40 Tage spaeter kommt. Deshalb werden beide Renditen getrennt
ausgewiesen: ab Handelstag und ab Meldetag. Fuer die Eignung zaehlt nur die
zweite, denn frueher kann niemand kopieren.
"""

from __future__ import annotations

import datetime as dt
import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..models import Disclosure
from ..prices import PriceProvider
from ..settings import ScoringConfig
from . import metrics

log = logging.getLogger(__name__)


@dataclass
class Evaluation:
    n_trades: int = 0
    n_closed: int = 0
    win_rate: float = 0.0
    win_rate_shrunk: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    alpha_from_trade: float = 0.0
    alpha_from_disclosure: float = 0.0
    median_hold_days: float = 0.0
    median_lag_days: float = 0.0
    max_drawdown: float = 0.0
    stop_usage_rate: float = 0.0
    avg_risk_pct: float = 0.0
    consistency: float = 0.0
    freshness: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


def evaluate_disclosure_actor(
    disclosures: list[Disclosure],
    prices: PriceProvider,
    cfg: ScoringConfig,
) -> Evaluation:
    """Politiker und Insider: Qualitaet aus dem Kursverlauf nach der Meldung."""
    ev = Evaluation()
    cutoff = dt.date.today() - dt.timedelta(days=cfg.lookback_days)
    rows = [d for d in disclosures if d.symbol and d.transaction_date and d.transaction_date >= cutoff]
    if not rows:
        return ev

    ev.n_trades = len(rows)
    horizon = max(cfg.horizons_days)
    lags: list[float] = []
    alpha_trade: list[float] = []
    alpha_disc: list[float] = []
    by_year: dict[str, list[float]] = defaultdict(list)
    per_horizon: dict[int, list[float]] = defaultdict(list)

    for d in rows:
        lag = d.lag_days
        if lag is not None and 0 <= lag <= 400:
            lags.append(float(lag))
        direction = 1.0 if d.side == "buy" else -1.0

        for h in cfg.horizons_days:
            raw = prices.forward_return(d.symbol, d.transaction_date, h, asset_class=d.asset_class)
            bench = prices.forward_return(cfg.benchmark, d.transaction_date, h)
            if raw is None:
                continue
            per_horizon[h].append(direction * (raw - (bench or 0.0)))

        a_trade = _alpha(prices, d.symbol, d.transaction_date, horizon, cfg, d.asset_class)
        if a_trade is not None:
            alpha_trade.append(direction * a_trade)

        disc_day = d.disclosed_at.date() if d.disclosed_at else None
        if disc_day:
            a_disc = _alpha(prices, d.symbol, disc_day, horizon, cfg, d.asset_class)
            if a_disc is not None:
                value = direction * a_disc
                alpha_disc.append(value)
                by_year[str(disc_day.year)].append(value)

    if not alpha_disc:
        # Ohne Meldedatum bleibt nur der Handelstag. Das ist optimistisch,
        # wird beim Gate aber ueber die fehlende Verzoegerung wieder eingefangen.
        alpha_disc = list(alpha_trade)

    ev.n_closed = len(alpha_disc)
    wins = sum(1 for a in alpha_disc if a > 0)
    ev.win_rate = wins / len(alpha_disc) if alpha_disc else 0.0
    ev.win_rate_shrunk = metrics.shrunk_rate(wins, len(alpha_disc), cfg.prior_win_rate, cfg.prior_weight)
    ev.avg_win, ev.avg_loss = metrics.avg_win_loss(alpha_disc)
    ev.profit_factor = metrics.profit_factor(alpha_disc)
    ev.expectancy = metrics.expectancy(alpha_disc)
    ev.alpha_from_trade = metrics.expectancy(alpha_trade)
    ev.alpha_from_disclosure = ev.expectancy
    ev.max_drawdown = metrics.max_drawdown(alpha_disc)
    ev.median_lag_days = statistics.median(lags) if lags else 0.0
    ev.median_hold_days = float(horizon)
    ev.consistency = metrics.consistency(by_year)
    last_active = max((d.transaction_date for d in rows), default=None)
    ev.freshness = metrics.freshness(last_active, cfg.recency_halflife_days)
    ev.stop_usage_rate = sum(1 for d in rows if d.stop_loss) / len(rows)
    ev.detail = {
        "per_horizon_mean": {str(h): round(metrics.expectancy(v), 4) for h, v in per_horizon.items()},
        "wilson_win_rate": round(metrics.wilson_lower_bound(wins, len(alpha_disc)), 4),
        "sharpe_like": round(metrics.sharpe_like(alpha_disc), 3),
        "last_trade": str(last_active) if last_active else None,
        "method": "alpha_vs_benchmark",
        "benchmark": cfg.benchmark,
    }
    return ev


def evaluate_execution_actor(
    disclosures: list[Disclosure],
    prices: PriceProvider,
    cfg: ScoringConfig,
) -> Evaluation:
    """Trader mit echten Fills: Rundlaeufe nach FIFO, Ergebnis in Prozent
    und, wo ein Stop bekannt ist, zusaetzlich in R."""
    ev = Evaluation()
    cutoff = dt.date.today() - dt.timedelta(days=cfg.lookback_days)
    rows = sorted(
        [d for d in disclosures if d.symbol and d.transaction_date and d.transaction_date >= cutoff],
        key=lambda d: (d.transaction_date, d.id or 0),
    )
    if not rows:
        return ev
    ev.n_trades = len(rows)

    open_lots: dict[str, list[dict]] = defaultdict(list)
    returns: list[float] = []
    r_multiples: list[float] = []
    holds: list[float] = []
    by_year: dict[str, list[float]] = defaultdict(list)
    with_stop = 0

    for d in rows:
        qty = d.quantity or 1.0
        price = d.price or prices.close_on(d.symbol, d.transaction_date, asset_class=d.asset_class)
        if not price:
            continue
        if d.stop_loss:
            with_stop += 1
        lots = open_lots[d.symbol]
        opposite = "sell" if d.side == "buy" else "buy"
        matched = [lot for lot in lots if lot["side"] == opposite]

        if not matched:
            lots.append(
                {
                    "side": d.side,
                    "qty": qty,
                    "price": price,
                    "date": d.transaction_date,
                    "stop": d.stop_loss,
                }
            )
            continue

        remaining = qty
        while remaining > 1e-12 and matched:
            lot = matched[0]
            take = min(remaining, lot["qty"])
            direction = 1.0 if lot["side"] == "buy" else -1.0
            pnl = direction * (price - lot["price"]) / lot["price"]
            returns.append(pnl)
            by_year[str(d.transaction_date.year)].append(pnl)
            holds.append(float((d.transaction_date - lot["date"]).days))
            rm = metrics.r_multiple(lot["price"], price, lot["stop"], lot["side"])
            if rm is not None:
                r_multiples.append(rm)
            lot["qty"] -= take
            remaining -= take
            if lot["qty"] <= 1e-12:
                lots.remove(lot)
                matched.pop(0)
        if remaining > 1e-12:
            lots.append(
                {
                    "side": d.side,
                    "qty": remaining,
                    "price": price,
                    "date": d.transaction_date,
                    "stop": d.stop_loss,
                }
            )

    if not returns:
        return evaluate_disclosure_actor(disclosures, prices, cfg)

    wins = sum(1 for r in returns if r > 0)
    ev.n_closed = len(returns)
    ev.win_rate = wins / len(returns)
    ev.win_rate_shrunk = metrics.shrunk_rate(wins, len(returns), cfg.prior_win_rate, cfg.prior_weight)
    ev.avg_win, ev.avg_loss = metrics.avg_win_loss(returns)
    ev.profit_factor = metrics.profit_factor(returns)
    ev.expectancy = metrics.expectancy(returns)
    ev.alpha_from_trade = ev.expectancy
    ev.alpha_from_disclosure = ev.expectancy
    ev.max_drawdown = metrics.max_drawdown(returns)
    ev.median_hold_days = statistics.median(holds) if holds else 0.0
    ev.median_lag_days = 0.0
    ev.consistency = metrics.consistency(by_year)
    ev.stop_usage_rate = with_stop / len(rows)
    ev.avg_risk_pct = _avg_risk_pct(rows)
    last_active = max((d.transaction_date for d in rows), default=None)
    ev.freshness = metrics.freshness(last_active, cfg.recency_halflife_days)
    ev.detail = {
        "avg_r": round(statistics.fmean(r_multiples), 3) if r_multiples else None,
        "r_sample": len(r_multiples),
        "wilson_win_rate": round(metrics.wilson_lower_bound(wins, len(returns)), 4),
        "sharpe_like": round(metrics.sharpe_like(returns), 3),
        "open_lots": {k: len(v) for k, v in open_lots.items() if v},
        "method": "fifo_round_trips",
    }
    return ev


def _alpha(
    prices: PriceProvider,
    symbol: str,
    start: dt.date,
    horizon: int,
    cfg: ScoringConfig,
    asset_class: str,
) -> float | None:
    raw = prices.forward_return(symbol, start, horizon, asset_class=asset_class)
    if raw is None:
        return None
    if asset_class == "crypto":
        return raw
    bench = prices.forward_return(cfg.benchmark, start, horizon)
    return raw - (bench or 0.0)


def _avg_risk_pct(rows: list[Disclosure]) -> float:
    """Wie viel Prozent Abstand liegt im Schnitt zwischen Einstieg und Stop."""
    risks = []
    for d in rows:
        if d.stop_loss and d.price:
            risks.append(abs(d.price - d.stop_loss) / d.price * 100)
    return statistics.fmean(risks) if risks else 0.0
