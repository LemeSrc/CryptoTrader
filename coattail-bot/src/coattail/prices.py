"""Kursdaten fuer Bewertung, Positionsgroesse und Stops.

Reihenfolge: lokaler Cache, dann yfinance, dann Stooq als Notnagel. Fuer Krypto
die oeffentlichen Binance-Klines, die brauchen keinen Key. Der Cache ist eine
schlichte CSV-Ablage. Bei der Bewertung werden dieselben Zeitreihen hunderte
Male gebraucht, und niemand muss dafuer hunderte Male ins Netz.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
from pathlib import Path

import pandas as pd

from .http import HttpClient

log = logging.getLogger(__name__)

# Offline-Modus. Erzeugt deterministische Zufallspfade statt echter Kurse.
# Gedacht fuer Tests und fuer den ersten Trockenlauf ohne Netz, damit sich
# Sizing, Stops und Ausfuehrung durchspielen lassen. Niemals im Echtbetrieb.
SYNTHETIC = os.getenv("COATTAIL_SYNTHETIC_PRICES", "").lower() in ("1", "true", "yes")

CACHE_DIR = Path("data/prices")
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
STOOQ = "https://stooq.com/q/d/l/"


class PriceProvider:
    def __init__(self, cache_dir: Path | str = CACHE_DIR, max_age_hours: float = 12.0) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_age = dt.timedelta(hours=max_age_hours)
        self._mem: dict[str, pd.Series] = {}

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

    # ------------------------------------------------------------------ Abruf
    def history(
        self, symbol: str, *, asset_class: str = "equity", years: float = 4.0
    ) -> pd.Series | None:
        key = f"{asset_class}:{symbol}"
        if key in self._mem:
            return self._mem[key]
        path = self._cache_path(symbol, asset_class)
        cached = self._read_cache(path)
        if cached is not None and not cached.empty:
            self._mem[key] = cached
            return cached

        if SYNTHETIC:
            series = self._synthetic_history(symbol, years)
        else:
            series = (
                self._crypto_history(symbol)
                if asset_class == "crypto"
                else self._equity_history(symbol, years)
            )
        if series is None or series.empty:
            return None
        series = series[~series.index.duplicated(keep="last")].sort_index()
        self._mem[key] = series
        if not SYNTHETIC:
            self._write_cache(path, series)
        return series

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
        try:
            import yfinance as yf

            data = yf.Ticker(symbol).history(period=f"{int(years * 365)}d", auto_adjust=True)
            if not data.empty:
                s = data["Close"].astype(float)
                s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
                return s
        except Exception as exc:  # noqa: BLE001
            log.debug("yfinance %s: %s", symbol, exc)
        return self._stooq_history(symbol)

    def _stooq_history(self, symbol: str) -> pd.Series | None:
        try:
            with HttpClient() as client:
                resp = client.get(STOOQ, params={"s": f"{symbol.lower()}.us", "i": "d"})
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
        series = self.history(symbol, asset_class=asset_class)
        if series is None or series.empty:
            return None
        return float(series.iloc[-1])

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
