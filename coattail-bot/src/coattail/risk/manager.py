"""Risikosteuerung. Die Stelle, an der ein Copy-Bot ueberlebt oder stirbt.

Ein kopiertes Signal ist erst dann ein Auftrag, wenn alle Pruefungen bestehen.
Die Groesse kommt nie aus dem Vorbild, sondern immer aus dem eigenen Kapital
und dem Abstand zum Stop. Wer einem Senator mit einem Depot im achtstelligen
Bereich eins zu eins in der Positionsgroesse folgt, ist nach einem schlechten
Monat fertig. Eins zu eins bezieht sich auf Titel und Richtung, nicht auf den
Betrag.

Zusaetzlich zwei Bremsen, die in der Praxis am haeufigsten fehlen:

Haeufung. Kopieren mehrere Vorbilder denselben Titel, entsteht ohne Deckel
still und leise eine Position in mehrfacher Groesse. Deshalb ein Limit pro
Ticker ueber alle Quellen hinweg.

Abschaltung. Tagesverlust und Gesamtrueckgang sind harte Grenzen. Wird eine
gerissen, macht der Bot nichts Neues mehr auf, bis jemand ihn bewusst wieder
freigibt.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import EquitySnapshot, Position, Signal
from ..prices import PriceProvider
from ..settings import AppConfig
from ..util import clamp

log = logging.getLogger(__name__)

KILL_SWITCH_KEY = "kill_switch"
FUTURE_TOLERANCE_MINUTES = 60  # Spielraum fuer Uhrenversatz bei den Quellen


@dataclass
class RiskDecision:
    approved: bool
    quantity: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_amount: float = 0.0
    reference_price: float | None = None
    reason: str = ""
    notes: list[str] = field(default_factory=list)


class RiskManager:
    def __init__(self, config: AppConfig, prices: PriceProvider) -> None:
        self.config = config
        self.risk = config.risk
        self.prices = prices

    # ------------------------------------------------------------ Kapital
    def equity(self, session: Session) -> float:
        snap = session.scalar(
            select(EquitySnapshot).order_by(EquitySnapshot.taken_at.desc()).limit(1)
        )
        return snap.equity if snap else self.risk.equity_base

    def day_pnl_pct(self, session: Session) -> float:
        today = dt.datetime.now(dt.UTC).date()
        rows = list(
            session.scalars(
                select(EquitySnapshot).order_by(EquitySnapshot.taken_at.asc())
            )
        )
        todays = [r for r in rows if r.taken_at.date() == today]
        if len(todays) < 2:
            return 0.0
        first, last = todays[0].equity, todays[-1].equity
        return (last / first - 1) * 100 if first else 0.0

    def drawdown_pct(self, session: Session) -> float:
        rows = list(session.scalars(select(EquitySnapshot.equity)))
        if not rows:
            return 0.0
        peak = max(rows)
        return (1 - rows[-1] / peak) * 100 if peak else 0.0

    # --------------------------------------------------------- Pruefungen
    def evaluate(self, session: Session, signal: Signal) -> RiskDecision:
        notes: list[str] = []

        blocked = self._hard_blocks(session, signal)
        if blocked:
            return RiskDecision(False, reason=blocked)

        # Der aktuelle Kurs zuerst. Der Preis in einer Kongressmeldung ist der
        # vom Handelstag, oft Wochen alt, und wuerde Groesse und Stop verzerren.
        price = self.prices.last_price(
            signal.symbol, asset_class=signal.asset_class
        ) or signal.reference_price
        if not price or price <= 0:
            return RiskDecision(False, reason=f"kein Kurs fuer {signal.symbol}")

        equity = self.equity(session)
        stop = signal.stop_loss or self._synthetic_stop(signal, price, notes)
        if not stop:
            if self.risk.require_stop_for_leverage and signal.asset_class == "crypto":
                return RiskDecision(False, reason="kein Stop ermittelbar, Hebelmarkt gesperrt")
            stop = price * (0.92 if signal.side == "buy" else 1.08)
            notes.append("Notfall-Stop mit 8 Prozent Abstand")

        stop_distance = abs(price - stop)
        if stop_distance <= 0:
            return RiskDecision(False, reason="Stopabstand ist null")

        factor = self._conviction_factor(signal)
        risk_amount = equity * (self.risk.risk_per_trade_pct / 100.0) * factor
        quantity = risk_amount / stop_distance
        notional = quantity * price

        max_notional = equity * (self.risk.max_position_pct / 100.0)
        if notional > max_notional:
            quantity = max_notional / price
            notional = max_notional
            risk_amount = quantity * stop_distance
            notes.append(f"Groesse auf {self.risk.max_position_pct} Prozent des Kapitals gedeckelt")

        headroom = self._ticker_headroom(session, signal.symbol, equity)
        if headroom <= 0:
            return RiskDecision(False, reason=f"Limit fuer {signal.symbol} bereits ausgeschoepft")
        if notional > headroom:
            quantity = headroom / price
            notional = headroom
            risk_amount = quantity * stop_distance
            notes.append("Groesse wegen bestehender Position im selben Titel reduziert")

        gross_left = self._gross_headroom(session, equity)
        if notional > gross_left:
            quantity = max(0.0, gross_left / price)
            notional = quantity * price
            notes.append("Groesse wegen Gesamtauslastung reduziert")

        if not self.config.execution.fractional_shares and signal.asset_class == "equity":
            quantity = float(int(quantity))

        if quantity <= 0 or notional < self.risk.min_order_notional:
            return RiskDecision(
                False,
                reason=f"Ordergroesse {notional:.2f} unter Mindestbetrag {self.risk.min_order_notional}",
            )

        take_profit = signal.take_profit or self._take_profit(price, stop, signal.side)
        return RiskDecision(
            approved=True,
            quantity=round(quantity, 8),
            stop_loss=round(stop, 6),
            take_profit=round(take_profit, 6) if take_profit else None,
            risk_amount=round(risk_amount, 2),
            reference_price=price,
            reason="freigegeben",
            notes=notes,
        )

    def _hard_blocks(self, session: Session, signal: Signal) -> str | None:
        from ..db import get_state

        state = get_state(session, KILL_SWITCH_KEY, {}) or {}
        if state.get("active"):
            return f"Notaus aktiv: {state.get('reason', 'ohne Angabe')}"

        if self.config.ticker_allowlist and signal.symbol not in self.config.ticker_allowlist:
            return f"{signal.symbol} steht nicht auf der Positivliste"
        if signal.symbol in self.config.ticker_blocklist:
            return f"{signal.symbol} steht auf der Sperrliste"
        if signal.side == "sell" and signal.intent == "open" and not self.config.execution.allow_short:
            return "Leerverkaeufe sind ausgeschaltet"
        if signal.asset_class == "option" and not self.config.execution.allow_options:
            return "Optionen sind ausgeschaltet"

        age_min = (dt.datetime.now(dt.UTC) - _aware(signal.event_at)).total_seconds() / 60
        if age_min > self.config.execution.max_signal_age_minutes:
            return f"Signal ist {age_min / 60:.1f} Stunden alt und damit zu spaet"
        # Ein Zeitstempel in der Zukunft ist ein Datenfehler der Quelle. Ohne
        # diese Pruefung rutscht er durch die Altersgrenze, weil die Differenz
        # negativ wird, und der Bot handelt auf einer Meldung, die es noch
        # gar nicht gibt.
        if age_min < -FUTURE_TOLERANCE_MINUTES:
            return f"Zeitstempel liegt {-age_min / 60:.1f} Stunden in der Zukunft"

        day_pnl = self.day_pnl_pct(session)
        if day_pnl <= -abs(self.risk.daily_loss_limit_pct):
            return f"Tagesverlustgrenze erreicht ({day_pnl:.2f} Prozent)"
        dd = self.drawdown_pct(session)
        if dd >= self.risk.max_drawdown_pct:
            return f"Maximaler Rueckgang erreicht ({dd:.2f} Prozent)"

        n_open = session.scalar(
            select(func.count()).select_from(Position).where(Position.closed_at.is_(None))
        ) or 0
        if n_open >= self.risk.max_open_positions:
            return f"bereits {n_open} offene Positionen"
        return None

    # ------------------------------------------------------------ Helfer
    def _conviction_factor(self, signal: Signal) -> float:
        """Gute Vorbilder bekommen mehr Gewicht, aber hoechstens das Doppelte
        der Grundgroesse und nie weniger als ein Viertel."""
        score_part = clamp((signal.actor_score - 50.0) / 40.0, 0.0, 1.0)
        base = 0.5 + score_part  # 0.5 bis 1.5
        return clamp(base * clamp(signal.conviction + 0.5, 0.5, 1.5), 0.25, 2.0)

    def _synthetic_stop(self, signal: Signal, price: float, notes: list[str]) -> float | None:
        atr = self.prices.atr(signal.symbol, asset_class=signal.asset_class)
        if not atr:
            return None
        distance = atr * self.risk.default_stop_atr_mult
        notes.append(f"Stop aus Schwankungsbreite abgeleitet ({distance / price:.1%} Abstand)")
        return price - distance if signal.side == "buy" else price + distance

    def _take_profit(self, price: float, stop: float, side: str) -> float | None:
        r = abs(price - stop) * self.risk.default_take_profit_r
        return price + r if side == "buy" else price - r

    def _ticker_headroom(self, session: Session, symbol: str, equity: float) -> float:
        cap = equity * (self.risk.max_ticker_exposure_pct / 100.0)
        used = 0.0
        for pos in session.scalars(
            select(Position).where(Position.symbol == symbol, Position.closed_at.is_(None))
        ):
            used += abs(pos.quantity) * (pos.avg_price or 0.0)
        return max(0.0, cap - used)

    def _gross_headroom(self, session: Session, equity: float) -> float:
        cap = equity * (self.risk.max_gross_exposure_pct / 100.0)
        used = sum(
            abs(p.quantity) * (p.avg_price or 0.0)
            for p in session.scalars(select(Position).where(Position.closed_at.is_(None)))
        )
        return max(0.0, cap - used)


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def trip_kill_switch(session: Session, reason: str) -> None:
    from ..db import set_state

    set_state(
        session,
        KILL_SWITCH_KEY,
        {"active": True, "reason": reason, "since": str(dt.datetime.now(dt.UTC))},
    )
    log.critical("Notaus ausgeloest: %s", reason)


def reset_kill_switch(session: Session) -> None:
    from ..db import set_state

    set_state(session, KILL_SWITCH_KEY, {"active": False, "reason": "", "since": None})
