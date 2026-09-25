"""Kursdaten fuer Bewertung, Positionsgroesse und Stops.

Reihenfolge fuer Aktien: lokaler Cache, dann Alpaca (wenn Schluessel in .env
stehen), dann Yahoo ueber yfinance, dann Stooq als Notnagel. Fuer Krypto die
oeffentlichen Binance-Klines, die brauchen keinen Key. Der Cache ist eine
schlichte CSV-Ablage. Bei der Bewertung werden dieselben Zeitreihen hunderte
Male gebraucht, und niemand muss dafuer hunderte Male ins Netz.

Yahoo drosselt Rechenzentren schnell und ohne Ankuendigung. Deshalb wird
Drosselung erkannt, sichtbar gemeldet und nicht mit "keine Daten"
verwechselt: ein gedrosselter Titel landet nicht auf der Fehlliste, und die
Bewertung bricht ab, statt mit Luecken falsche Noten zu vergeben.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Iterable
from pathlib import Path

import httpx
import pandas as pd

from .http import HttpClient

log = logging.getLogger(__name__)

# yfinance meldet jeden unbekannten oder eingestellten Titel als ERROR. Bei
# alten Kongressmeldungen sind das viele (umbenannte Firmen, Anleihen), und das
# ueberdeckt im Journal die Meldungen, auf die es ankommt.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# Offline-Modus. Erzeugt deterministische Zufallspfade statt echter Kurse.
# Gedacht fuer Tests und fuer den ersten Trockenlauf ohne Netz, damit sich
# Sizing, Stops und Ausfuehrung durchspielen lassen. Niemals im Echtbetrieb.
SYNTHETIC = os.getenv("COATTAIL_SYNTHETIC_PRICES", "").lower() in ("1", "true", "yes")

CACHE_DIR = Path("data/prices")
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/price"
STOOQ = "https://stooq.com/q/d/l/"
ALPACA_DATA = "https://data.alpaca.markets/v2/stocks"
# Was Alpaca als Kuerzel annimmt. Ein einziges ungueltiges Kuerzel laesst sonst
# den ganzen Block mit 400 scheitern.
ALPACA_SYMBOL = re.compile(r"[A-Z][A-Z0-9]{0,5}(\.[A-Z]{1,2})?")

# Wie lange nach einer Drosselung durch Yahoo keine Abrufe dorthin gehen.
YAHOO_PAUSE = dt.timedelta(minutes=45)
# So viele leere Antworten am Stueck sind kein Zufall mehr, sondern Ausfall
# oder stille Drosselung. Eingestellte Titel kommen nie in solchen Serien.
YAHOO_EMPTY_STREAK = 25


class RateLimited(RuntimeError):
    """Die Kursquelle hat abgewiesen. Kein Beleg dafuer, dass Daten fehlen."""


class PriceProvider:
    """Tagesreihen fuer die Bewertung, Live-Kurse fuer Orders und Stops.

    Der Dienst laeuft wochenlang am Stueck. Ein Speicher-Cache ohne Ablauf
    wuerde deshalb ab dem zweiten Tag mit Kursen von gestern rechnen, Stops
    verpassen und Papierorders zu alten Preisen fuellen. Deshalb verfaellt
    alles: Tagesreihen nach mem_ttl, Live-Kurse nach quote_ttl.
    """

    def __init__(
        self,
        cache_dir: Path | str = CACHE_DIR,
        max_age_hours: float = 12.0,
        mem_ttl_minutes: float = 60.0,
        quote_ttl_seconds: float = 60.0,
        alpaca_key: str | None = None,
        alpaca_secret: str | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_age = dt.timedelta(hours=max_age_hours)
        self.mem_ttl = dt.timedelta(minutes=mem_ttl_minutes)
        self.quote_ttl = dt.timedelta(seconds=quote_ttl_seconds)
        self._mem: dict[str, tuple[dt.datetime, pd.Series]] = {}
        self._quotes: dict[str, tuple[dt.datetime, float]] = {}
        # Titel ohne Kursdaten. Ohne dieses Gedaechtnis fragt die Bewertung
        # fuer jede Meldung zehnmal erneut im Netz nach, jedes Mal mit
        # Wartezeiten. Genau das hat den ersten Lauf auf Stunden gestreckt.
        self._missing_path = self.cache_dir / "_missing.json"
        self._missing: dict[str, float] = self._load_missing()
        self._alpaca_headers = (
            {"APCA-API-KEY-ID": alpaca_key, "APCA-API-SECRET-KEY": alpaca_secret}
            if alpaca_key and alpaca_secret
            else None
        )
        self._alpaca_feed = "sip"
        self._yahoo_blocked_until: dt.datetime | None = None
        self._yahoo_empty_streak = 0

    @property
    def uses_alpaca(self) -> bool:
        return self._alpaca_headers is not None

    def yahoo_blocked(self) -> bool:
        until = self._yahoo_blocked_until
        return until is not None and dt.datetime.now(dt.UTC) < until

    def _block_yahoo(self, reason: str = "drosselt die Abrufe von diesem Server") -> None:
        if not self.yahoo_blocked():
            log.warning(
                "Yahoo %s. Pause %d Minuten, Bewertungen warten so lange. "
                "Abhilfe auf Dauer: Alpaca-Schluessel in .env.",
                reason, int(YAHOO_PAUSE.total_seconds() // 60),
            )
        self._yahoo_blocked_until = dt.datetime.now(dt.UTC) + YAHOO_PAUSE

    # ------------------------------------------------------------------ Cache
    def _cache_path(self, symbol: str, asset_class: str) -> Path:
        safe = symbol.replace("/", "_").replace(".", "_")
        return self.cache_dir / f"{asset_class}_{safe}.csv"

    def _read_cache(self, path: Path) -> pd.Series | None:
        if not path.exists():
            return None
        age = dt.datetime.now() - dt.datetime.fromtimestamp(path.stat().st_mtime)
        if age > self.max_age:
            return None
        try:
            df = pd.read_csv(path, parse_dates=["date"], index_col="date")
            return df["close"].astype(float)
        except Exception:  # noqa: BLE001
            return None

    def _write_cache(self, path: Path, series: pd.Series) -> None:
        try:
            series.rename("close").rename_axis("date").to_frame().to_csv(path)
        except Exception as exc:  # noqa: BLE001
            log.debug("Cache nicht schreibbar (%s): %s", path, exc)

    def _load_missing(self) -> dict[str, float]:
        try:
            raw = json.loads(self._missing_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        cutoff = time.time() - self.max_age.total_seconds()
        return {k: float(v) for k, v in raw.items() if float(v) > cutoff}

    def _is_missing(self, key: str) -> bool:
        stamp = self._missing.get(key)
        return stamp is not None and time.time() - stamp < self.max_age.total_seconds()

    def _mark_missing(self, key: str) -> None:
        self._missing[key] = time.time()
        try:
            self._missing_path.write_text(json.dumps(self._missing), encoding="utf-8")
        except OSError as exc:
            log.debug("Fehlliste nicht schreibbar: %s", exc)

    # ------------------------------------------------------------------ Abruf
    def prefetch(self, symbols: Iterable[str], *, years: float = 4.0, chunk: int = 100) -> int:
        """Alle Aktienreihen laden, bevor die Bewertung sie einzeln braucht.

        Mit Alpaca in Bloecken zu hundert Titeln pro Abruf, sonst Titel fuer
        Titel ueber Yahoo mit kurzer Pause dazwischen. Fortschritt steht alle
        hundert Titel im Log. Wird Yahoo gedrosselt, endet der Lauf sofort und
        meldet das; history() holt spaeter nach, was fehlt.
        Liefert die Zahl der neu geladenen Reihen.
        """
        if SYNTHETIC:
            return 0
        todo = []
        for symbol in sorted(set(symbols)):
            key = f"equity:{symbol}"
            if self._is_missing(key) or key in self._mem:
                continue
            if self._read_cache(self._cache_path(symbol, "equity")) is not None:
                continue
            todo.append(symbol)
        if not todo:
            log.info("Kursreihen: alle %d Titel schon im Cache", len(set(symbols)))
            return 0

        source = "Alpaca" if self.uses_alpaca else "Yahoo"
        log.info("Lade Kursreihen fuer %d Titel ueber %s", len(todo), source)
        loaded = 0
        start_day = dt.date.today() - dt.timedelta(days=int(years * 365))

        if self.uses_alpaca:
            candidates = [s for s in todo if ALPACA_SYMBOL.fullmatch(s)]
            for i in range(0, len(candidates), chunk):
                part = candidates[i : i + chunk]
                try:
                    found = self._alpaca_bars(part, start_day)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (401, 403):
                        log.warning("Alpaca lehnt die Schluessel ab, weiter mit Yahoo: %s", exc)
                        break
                    log.warning("Alpaca-Block uebersprungen: %s", exc)
                    continue
                except Exception as exc:  # noqa: BLE001
                    log.warning("Alpaca-Abruf fehlgeschlagen, weiter mit Yahoo: %s", exc)
                    break
                for symbol, series in found.items():
                    self._store(symbol, series)
                    loaded += 1
                log.info(
                    "Kursreihen: %d von %d Titeln abgefragt", min(i + chunk, len(candidates)), len(candidates)
                )
            todo = [s for s in todo if f"equity:{s}" not in self._mem]
            if not todo:
                return loaded

        missing = 0
        # Leere Antworten erst als fehlend merken, wenn danach wieder etwas
        # ankommt. Endet der Lauf in einer Serie von Leeren, war es ein Ausfall.
        pending: list[str] = []
        for n, symbol in enumerate(todo, start=1):
            if self.yahoo_blocked():
                log.warning("Kursreihen abgebrochen nach %d von %d Titeln (Yahoo drosselt)", n - 1, len(todo))
                break
            try:
                series = self._yahoo_history(symbol, years)
            except RateLimited:
                continue
            if series is None:
                pending.append(symbol)
            else:
                for gone in pending:
                    self._mark_missing(f"equity:{gone}")
                missing += len(pending)
                pending.clear()
                self._store(symbol, series)
                loaded += 1
            if n % 100 == 0 or n == len(todo):
                log.info("Kursreihen: %d von %d Titeln, %d ohne Daten", n, len(todo), missing)
            time.sleep(0.3)
        return loaded

    def _store(self, symbol: str, series: pd.Series, asset_class: str = "equity") -> pd.Series:
        series = series[~series.index.duplicated(keep="last")].sort_index()
        self._mem[f"{asset_class}:{symbol}"] = (dt.datetime.now(dt.UTC), series)
        if not SYNTHETIC:
            self._write_cache(self._cache_path(symbol, asset_class), series)
        return series

    def history(
        self, symbol: str, *, asset_class: str = "equity", years: float = 4.0
    ) -> pd.Series | None:
        key = f"{asset_class}:{symbol}"
        now = dt.datetime.now(dt.UTC)
        hit = self._mem.get(key)
        if hit and now - hit[0] < self.mem_ttl:
            return hit[1]
        path = self._cache_path(symbol, asset_class)
        cached = self._read_cache(path)
        if cached is not None and not cached.empty:
            self._mem[key] = (now, cached)
            return cached
        if not SYNTHETIC and self._is_missing(key):
            return None

        if SYNTHETIC:
            series = self._synthetic_history(symbol, years)
        elif asset_class == "crypto":
            series = self._crypto_history(symbol)
        else:
            try:
                series = self._equity_history(symbol, years)
            except RateLimited:
                return None  # nicht als fehlend merken, spaeter erneut
        if series is None or series.empty:
            if not SYNTHETIC:
                self._mark_missing(key)
            return None
        return self._store(symbol, series, asset_class)

    def _synthetic_history(self, symbol: str, years: float) -> pd.Series:
        """Aus dem Kuerzel abgeleiteter Zufallspfad. Gleicher Ticker, gleiche Reihe."""
        import numpy as np

        seed = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        n = int(years * 252)
        drift = 0.0003 + (seed % 7) * 0.00005
        vol = 0.011 + (seed % 5) * 0.002
        steps = rng.normal(drift, vol, n)
        start_price = 20 + (seed % 400)
        path = start_price * np.exp(np.cumsum(steps))
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
        return pd.Series(path, index=idx)

    def _equity_history(self, symbol: str, years: float) -> pd.Series | None:
        """Eine Reihe einzeln: Alpaca, Yahoo, Stooq.

        Wirft RateLimited, wenn Yahoo drosselt und kein anderer Weg etwas
        geliefert hat. Dann ist unklar, ob es den Titel gibt.
        """
        if self.uses_alpaca:
            try:
                found = self._alpaca_bars([symbol], dt.date.today() - dt.timedelta(days=int(years * 365)))
                if symbol in found:
                    return found[symbol]
            except Exception as exc:  # noqa: BLE001
                log.debug("alpaca %s: %s", symbol, exc)
        limited = False
        try:
            series = self._yahoo_history(symbol, years)
            if series is not None:
                return series
        except RateLimited:
            limited = True
        series = self._stooq_history(symbol)
        if series is not None and not series.empty:
            return series
        if limited:
            raise RateLimited(symbol)
        return None

    def _yahoo_history(self, symbol: str, years: float) -> pd.Series | None:
        """None heisst: Yahoo kennt keine Daten. Drosselung wirft RateLimited."""
        if self.yahoo_blocked():
            raise RateLimited(symbol)
        try:
            import yfinance as yf
            from yfinance.exceptions import YFRateLimitError
        except ImportError:
            return None
        try:
            data = yf.Ticker(yahoo_symbol(symbol)).history(
                period=f"{int(years * 365)}d", auto_adjust=True
            )
        except YFRateLimitError as exc:
            self._block_yahoo()
            raise RateLimited(symbol) from exc
        except Exception as exc:  # noqa: BLE001
            log.debug("yfinance %s: %s", symbol, exc)
            data = None
        if data is None or data.empty or "Close" not in data:
            self._yahoo_empty_streak += 1
            if self._yahoo_empty_streak >= YAHOO_EMPTY_STREAK:
                self._yahoo_empty_streak = 0
                self._block_yahoo(
                    f"liefert {YAHOO_EMPTY_STREAK} Mal am Stueck nichts (Ausfall oder Drosselung)"
                )
                raise RateLimited(symbol)
            return None
        self._yahoo_empty_streak = 0
        s = data["Close"].dropna().astype(float)
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        return s if not s.empty else None

    def _alpaca_bars(self, symbols: list[str], start: dt.date) -> dict[str, pd.Series]:
        """Tagesschlusskurse, dividenden- und splitbereinigt, viele Titel pro Abruf.

        Das kostenlose Alpaca-Konto erlaubt den vollstaendigen Markt (SIP) mit
        15 Minuten Verzoegerung, fuer Tagesreihen reicht das. Lehnt Alpaca SIP
        ab, geht es mit dem IEX-Feed weiter.
        """
        end = (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        closes: dict[str, dict[str, float]] = {}
        token: str | None = None
        with HttpClient(headers=self._alpaca_headers) as client:
            while True:
                params = {
                    "symbols": ",".join(symbols),
                    "timeframe": "1Day",
                    "start": start.isoformat(),
                    "end": end,
                    "limit": 10000,
                    "adjustment": "all",
                    "feed": self._alpaca_feed,
                }
                if token:
                    params["page_token"] = token
                resp = client.get(f"{ALPACA_DATA}/bars", params=params)
                if resp.status_code == 403 and self._alpaca_feed == "sip":
                    log.info("Alpaca erlaubt keinen SIP-Feed, nehme IEX")
                    self._alpaca_feed = "iex"
                    continue
                resp.raise_for_status()
                payload = resp.json()
                for sym, bars in (payload.get("bars") or {}).items():
                    target = closes.setdefault(sym, {})
                    for bar in bars or []:
                        target[str(bar["t"])[:10]] = float(bar["c"])
                token = payload.get("next_page_token")
                if not token:
                    break
        out: dict[str, pd.Series] = {}
        for sym, values in closes.items():
            if values:
                series = pd.Series(values, dtype=float)
                series.index = pd.to_datetime(series.index)
                out[sym] = series.sort_index()
        return out

    def _stooq_history(self, symbol: str) -> pd.Series | None:
        """Notnagel mit genau einem Versuch. Mit Wiederholungen und Wartezeiten
        kostete jeder unbekannte Titel hier bis zu zwei Minuten."""
        try:
            from .http import user_agent

            resp = httpx.get(
                STOOQ,
                params={"s": f"{symbol.lower()}.us", "i": "d"},
                headers={"User-Agent": user_agent()},
                timeout=10.0,
                follow_redirects=True,
            )
            if resp.status_code != 200 or "Date" not in resp.text[:64]:
                return None
            from io import StringIO

            df = pd.read_csv(StringIO(resp.text), parse_dates=["Date"])
            return pd.Series(df["Close"].astype(float).values, index=df["Date"].values)
        except Exception as exc:  # noqa: BLE001
            log.debug("stooq %s: %s", symbol, exc)
            return None

    def _crypto_history(self, symbol: str) -> pd.Series | None:
        pair = symbol if symbol.endswith(("USDT", "USDC", "USD")) else f"{symbol}USDT"
        try:
            with HttpClient() as client:
                rows = client.json(
                    BINANCE_KLINES, params={"symbol": pair, "interval": "1d", "limit": 1000}
                )
            idx = [dt.datetime.fromtimestamp(r[0] / 1000).date() for r in rows]
            vals = [float(r[4]) for r in rows]
            return pd.Series(vals, index=pd.to_datetime(idx))
        except Exception as exc:  # noqa: BLE001
            log.debug("binance %s: %s", pair, exc)
            return None

    # ------------------------------------------------------------- Abfragen
    def close_on(
        self, symbol: str, day: dt.date, *, asset_class: str = "equity"
    ) -> float | None:
        """Letzter Schlusskurs am oder vor dem Stichtag."""
        series = self.history(symbol, asset_class=asset_class)
        if series is None or series.empty:
            return None
        stamp = pd.Timestamp(day)
        window = series[series.index <= stamp]
        if window.empty:
            return None
        return float(window.iloc[-1])

    def last_price(self, symbol: str, *, asset_class: str = "equity") -> float | None:
        """Aktueller Kurs. Live, wenn erreichbar, sonst letzter Tagesschluss."""
        live = self.quote(symbol, asset_class=asset_class)
        if live:
            return live
        series = self.history(symbol, asset_class=asset_class)
        if series is None or series.empty:
            return None
        return float(series.iloc[-1])

    def quote(self, symbol: str, *, asset_class: str = "equity") -> float | None:
        if SYNTHETIC:
            return None
        key = f"{asset_class}:{symbol}"
        now = dt.datetime.now(dt.UTC)
        hit = self._quotes.get(key)
        if hit and now - hit[0] < self.quote_ttl:
            return hit[1]
        price = self._crypto_quote(symbol) if asset_class == "crypto" else self._equity_quote(symbol)
        if price and price > 0:
            self._quotes[key] = (now, price)
            return price
        return None

    def _equity_quote(self, symbol: str) -> float | None:
        """Laufender Kurs. Alpaca liefert den letzten Umsatz an der IEX in
        Echtzeit, Yahoo den laufenden Kurs. Ausserhalb der Handelszeit ist es
        jeweils der letzte Schluss."""
        if self.uses_alpaca:
            try:
                with HttpClient(headers=self._alpaca_headers) as client:
                    payload = client.json(
                        f"{ALPACA_DATA}/{symbol}/trades/latest", params={"feed": "iex"}
                    )
                value = (payload.get("trade") or {}).get("p")
                if value:
                    return float(value)
            except Exception as exc:  # noqa: BLE001
                log.debug("Alpaca-Kurs %s: %s", symbol, exc)
        if self.yahoo_blocked():
            return None
        try:
            import yfinance as yf

            value = yf.Ticker(yahoo_symbol(symbol)).fast_info.last_price
            return float(value) if value else None
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "YFRateLimitError":
                self._block_yahoo()
            log.debug("Live-Kurs %s: %s", symbol, exc)
            return None

    def _crypto_quote(self, symbol: str) -> float | None:
        pair = symbol if symbol.endswith(("USDT", "USDC", "USD")) else f"{symbol}USDT"
        try:
            with HttpClient() as client:
                payload = client.json(BINANCE_TICKER, params={"symbol": pair})
            return float(payload["price"])
        except Exception as exc:  # noqa: BLE001
            log.debug("Binance-Kurs %s: %s", pair, exc)
            return None

    def forward_return(
        self,
        symbol: str,
        start: dt.date,
        horizon_days: int,
        *,
        asset_class: str = "equity",
    ) -> float | None:
        """Rendite ueber den Zeitraum nach dem Stichtag."""
        entry = self.close_on(symbol, start, asset_class=asset_class)
        exit_price = self.close_on(
            symbol, start + dt.timedelta(days=horizon_days), asset_class=asset_class
        )
        if not entry or not exit_price or entry <= 0:
            return None
        return (exit_price / entry) - 1.0

    def atr(
        self, symbol: str, window: int = 14, *, asset_class: str = "equity"
    ) -> float | None:
        """Naeherung ueber Tagesschlusskurse. Reicht fuer die Stopweite."""
        series = self.history(symbol, asset_class=asset_class)
        if series is None or len(series) < window + 1:
            return None
        return float(series.diff().abs().tail(window).mean())


def yahoo_symbol(symbol: str) -> str:
    """Yahoo schreibt Aktienklassen mit Bindestrich: BRK.B heisst dort BRK-B."""
    return symbol.replace(".", "-").replace("/", "-")

