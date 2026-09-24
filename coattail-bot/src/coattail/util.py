from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any

_TICKER_CLEAN = re.compile(r"[^A-Z0-9.\-/]")


def fingerprint(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def clean_symbol(symbol: str | None) -> str | None:
    """Vereinheitlicht Tickerschreibweisen aus sehr unterschiedlichen Quellen.

    Capitol Trades haengt die Boerse an ('LMT:US'), andere Feeds liefern den
    Ticker mit Firmennamen dahinter ('AAPL - Apple Inc'). Beides wird auf das
    reine Kuerzel gekuerzt. Alles laenger als zwoelf Zeichen ist kein Ticker
    und wird verworfen, statt eine Order auf einen Fantasietitel auszuloesen.
    """
    if not symbol:
        return None
    s = symbol.strip().upper()
    for junk in ("--", "N/A", "NONE", "", "-"):
        if s == junk:
            return None
    s = s.split(":")[0]
    s = s.split(" ")[0]
    s = _TICKER_CLEAN.sub("", s)
    if not s or len(s) > 12:
        return None
    return s


def parse_amount_range(text: str | None) -> tuple[float | None, float | None]:
    """'$1,001 - $15,000' wird zu (1001.0, 15000.0)."""
    if not text:
        return None, None
    nums = re.findall(r"[\d,]+(?:\.\d+)?", str(text))
    vals = [float(n.replace(",", "")) for n in nums if n.strip(",")]
    if not vals:
        return None, None
    if len(vals) == 1:
        return vals[0], vals[0]
    return min(vals), max(vals)


def to_utc(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.UTC)
    from dateutil import parser as dateparser

    try:
        parsed = dateparser.parse(str(value))
    except (ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def to_date(value: Any) -> dt.date | None:
    parsed = to_utc(value)
    return parsed.date() if parsed else None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"
