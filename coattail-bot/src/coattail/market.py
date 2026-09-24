"""Handelszeiten der US-Boerse.

Der Papierbroker kannte bisher keine Uhr und fuellte Aktienorders auch am
Sonntag um drei Uhr nachts zum Freitagsschluss. Im Papierhandel sieht das
harmlos aus, verfaelscht aber genau die Zahl, auf die es ankommt: ob sich
das Nachhandeln lohnt. Echte Orders koennen erst zur Eroeffnung laufen, also
auch die simulierten.

Feiertage stehen hier fest drin. Das ist schlicht und muss einmal im Jahr
nachgetragen werden, erspart aber eine Abhaengigkeit fuer eine Handvoll Tage.
Verkuerzte Tage (etwa nach Thanksgiving) werden wie volle behandelt.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
OPEN = dt.time(9, 30)
CLOSE = dt.time(16, 0)

NYSE_HOLIDAYS = {
    # 2026
    dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16), dt.date(2026, 4, 3),
    dt.date(2026, 5, 25), dt.date(2026, 6, 19), dt.date(2026, 7, 3), dt.date(2026, 9, 7),
    dt.date(2026, 11, 26), dt.date(2026, 12, 25),
    # 2027
    dt.date(2027, 1, 1), dt.date(2027, 1, 18), dt.date(2027, 2, 15), dt.date(2027, 3, 26),
    dt.date(2027, 5, 31), dt.date(2027, 6, 18), dt.date(2027, 7, 5), dt.date(2027, 9, 6),
    dt.date(2027, 11, 25), dt.date(2027, 12, 24),
}


def us_market_open(now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now(dt.UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.UTC)
    local = now.astimezone(NEW_YORK)
    if local.weekday() >= 5 or local.date() in NYSE_HOLIDAYS:
        return False
    return OPEN <= local.time() < CLOSE
