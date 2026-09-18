"""Kennzahlen als reine Funktionen. Keine Datenbank, keine Netzwerkzugriffe,
damit sie sich einzeln testen lassen.

Zwei Dinge, die hier bewusst anders laufen als in den meisten Copy-Bots:

Kleine Stichproben werden bestraft, nicht gefeiert. Wer sieben von zehn Trades
gewinnt, hat keine Trefferquote von 70 Prozent, sondern zu wenig Daten. Die
geschrumpfte Quote zieht solche Werte in Richtung Muenzwurf, bis genug Trades
zusammenkommen.

Gemessen wird gegen den Markt, nicht absolut. Wer 2023 einen Technologiewert
gekauft hat, lag im Plus. Das war der Index, nicht das Koennen. Interessant ist
nur, was ueber dem Vergleichsindex uebrig bleibt.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections.abc import Sequence


def shrunk_rate(wins: int, n: int, prior: float = 0.5, weight: float = 20.0) -> float:
    """Trefferquote mit Beta-Prior. Bei n=0 kommt der Prior heraus."""
    if n <= 0:
        return prior
    return (wins + prior * weight) / (n + weight)


def wilson_lower_bound(wins: int, n: int, z: float = 1.64) -> float:
    """Untere Grenze des Konfidenzintervalls. Die pessimistische Lesart
    einer Trefferquote, und die einzige, auf die man Geld setzen sollte."""
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1 + z**2 / n
    centre = p + z**2 / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z**2 / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def profit_factor(returns: Sequence[float]) -> float:
    gains = sum(r for r in returns if r > 0)
    losses = abs(sum(r for r in returns if r < 0))
    if losses == 0:
        return float(gains > 0) * 3.0  # gedeckelt, sonst dominiert ein Zufall
    return gains / losses


def expectancy(returns: Sequence[float]) -> float:
    """Erwarteter Ertrag pro Trade."""
    return statistics.fmean(returns) if returns else 0.0


def avg_win_loss(returns: Sequence[float]) -> tuple[float, float]:
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    return (
        statistics.fmean(wins) if wins else 0.0,
        statistics.fmean(losses) if losses else 0.0,
    )


def max_drawdown(returns: Sequence[float]) -> float:
    """Groesster Rueckgang einer aus den Trades aufgebauten Kapitalkurve."""
    if not returns:
        return 0.0
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for r in returns:
        equity *= 1 + r
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    return abs(worst)


def consistency(returns_by_period: dict[str, list[float]]) -> float:
    """Anteil der Zeitraeume mit positivem Ergebnis.

    Ein Trader mit drei guten Jahren ist etwas anderes als einer mit einem
    Volltreffer und zwei mageren Jahren, auch wenn die Summe gleich aussieht.
    """
    if not returns_by_period:
        return 0.0
    positive = sum(1 for rs in returns_by_period.values() if rs and sum(rs) > 0)
    return positive / len(returns_by_period)


def freshness(last_active: dt.date | None, halflife_days: float = 365.0) -> float:
    """1.0 bei Aktivitaet heute, 0.5 nach einer Halbwertszeit."""
    if last_active is None:
        return 0.0
    age = (dt.date.today() - last_active).days
    if age < 0:
        return 1.0
    return 0.5 ** (age / max(halflife_days, 1.0))


def sharpe_like(returns: Sequence[float]) -> float:
    if len(returns) < 3:
        return 0.0
    sd = statistics.pstdev(returns)
    if sd == 0:
        return 0.0
    return statistics.fmean(returns) / sd


def r_multiple(entry: float, exit_price: float, stop: float | None, side: str) -> float | None:
    """Ergebnis in Vielfachen des eingegangenen Risikos.

    Das ist die ehrlichste Zahl im Copy-Trading: sie sagt, ob jemand seine
    Verluste kurz haelt, und genau das unterscheidet Glueck von Handwerk.
    """
    if not stop or entry <= 0:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    direction = 1 if side == "buy" else -1
    return direction * (exit_price - entry) / risk


def normalize(value: float, low: float, high: float) -> float:
    """Auf 0..1 skalieren, ausserhalb der Grenzen abgeschnitten."""
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))
