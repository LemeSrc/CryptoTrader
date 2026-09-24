"""Benachrichtigungen ueber Telegram.

Ein Bot, der still im Hintergrund laeuft, wird nicht kontrolliert. Deshalb
gehen Auftraege, Ablehnungen mit ungewoehnlichem Grund und jede Abschaltung
sofort aufs Telefon, dazu einmal am Tag eine Zusammenfassung.

Faellt die Benachrichtigung aus, laeuft der Handel weiter. Umgekehrt nicht:
ein Notaus wird immer gemeldet, notfalls beim naechsten Versuch.
"""

from __future__ import annotations

import datetime as dt
import logging

from ..http import HttpClient
from ..models import Order, Signal
from ..settings import AppConfig, Secrets

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, config: AppConfig, secrets: Secrets) -> None:
        self.config = config
        self.token = secrets.telegram_bot_token
        self.chat_id = secrets.telegram_chat_id
        self.enabled = bool(config.notify.telegram_enabled and self.token and self.chat_id)
        if config.notify.telegram_enabled and not self.enabled:
            log.warning("Telegram ist eingeschaltet, aber Token oder Chat-ID fehlen")

    def _send(self, text: str, event: str) -> None:
        if not self.enabled or event not in self.config.notify.notify_on:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            with HttpClient(timeout=10.0) as client:
                client.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": text[:4000],
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram nicht erreichbar: %s", exc)

    def order(self, signal: Signal, order: Order, dry_run: bool) -> None:
        tag = "Trockenlauf" if dry_run else "Auftrag"
        actor = signal.payload.get("actor") or signal.payload.get("author") or signal.origin
        self._send(
            f"<b>{tag}</b>\n"
            f"{signal.side.upper()} {order.quantity:.4f} {signal.symbol}\n"
            f"Vorbild: {actor} (Note {signal.actor_score:.0f})\n"
            f"Stop {order.stop_loss or 0:.2f} | Ziel {order.take_profit or 0:.2f}\n"
            f"Risiko {order.risk_amount or 0:.2f}",
            "order",
        )

    def rejection(self, signal: Signal, reason: str) -> None:
        self._send(
            f"<b>Abgelehnt</b>\n{signal.side.upper()} {signal.symbol}\nGrund: {reason}",
            "rejection",
        )

    def kill_switch(self, reason: str) -> None:
        # Der Notaus ignoriert die Filterliste bewusst.
        if not self.enabled:
            log.critical("Notaus ohne Benachrichtigungskanal: %s", reason)
            return
        original = self.config.notify.notify_on
        self.config.notify.notify_on = [*original, "kill_switch"]
        try:
            self._send(f"<b>NOTAUS</b>\n{reason}\nEs werden keine neuen Positionen eroeffnet.", "kill_switch")
        finally:
            self.config.notify.notify_on = original

    def daily_summary(self, lines: list[str]) -> None:
        stamp = dt.datetime.now(dt.UTC).strftime("%d.%m.%Y")
        self._send(f"<b>Tagesbericht {stamp}</b>\n" + "\n".join(lines), "daily_summary")

    def message(self, text: str) -> None:
        self._send(text, "order")
