"""
Anbindung an die öffentliche Binance REST-API.

Es werden ausschließlich öffentliche Endpunkte verwendet -> KEIN API-Key nötig
und kein Risiko. Geliefert werden:
  * die Top-Coins nach 24h-Handelsvolumen
  * Candlestick-Daten (Klines) als pandas DataFrame
"""

import time
import requests
import pandas as pd

import config

BASE_URL = "https://api.binance.com"
# Fallback-Hosts, falls der Haupt-Host (z.B. regional) blockiert ist
HOSTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api-gcp.binance.com",
    "https://data-api.binance.vision",  # reiner Markt-Daten-Host
]

_session = requests.Session()
_session.headers.update({"User-Agent": "CryptoTraderBot/1.0"})


def _get(path, params=None, retries=3):
    """GET-Request mit Host-Fallback und einfachem Retry."""
    last_err = None
    for host in HOSTS:
        url = host + path
        for attempt in range(retries):
            try:
                r = _session.get(url, params=params, timeout=15)
                if r.status_code == 200:
                    return r.json()
                # 429/418 = Rate-Limit -> kurz warten und neuen Versuch
                if r.status_code in (429, 418):
                    time.sleep(2 * (attempt + 1))
                    continue
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(1)
        # nächster Host
    raise RuntimeError(f"Binance-Request fehlgeschlagen ({path}): {last_err}")


def ping():
    """Verbindungstest. Gibt True zurück, wenn die API erreichbar ist."""
    try:
        _get("/api/v3/ping")
        return True
    except RuntimeError:
        return False


def get_top_symbols(n=None, quote=None):
    """Liefert die n liquidesten Handelspaare gegen die Quote-Währung.

    Sortiert nach 24h-Quote-Volumen (also tatsächlichem USDT-Umsatz).
    """
    n = n or config.TOP_N_SYMBOLS
    quote = quote or config.QUOTE_ASSET
    data = _get("/api/v3/ticker/24hr")

    rows = []
    for t in data:
        sym = t["symbol"]
        if not sym.endswith(quote):
            continue
        if sym in config.SYMBOL_BLACKLIST:
            continue
        if any(sym.endswith(suf) for suf in config.SYMBOL_EXCLUDE_SUFFIXES):
            continue
        try:
            qvol = float(t["quoteVolume"])
        except (KeyError, ValueError):
            continue
        if qvol <= 0:
            continue
        rows.append((sym, qvol))

    rows.sort(key=lambda x: x[1], reverse=True)
    return [sym for sym, _ in rows[:n]]


def get_klines(symbol, interval, limit=None):
    """Holt Candlestick-Daten und gibt sie als DataFrame zurück.

    Spalten: open_time (datetime, UTC), open, high, low, close, volume, close_time
    """
    limit = limit or config.KLINE_LIMIT
    raw = _get(
        "/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
    )
    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(
        raw,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_base", "taker_quote", "ignore",
        ],
    )
    # taker_base = von Taker (Marktorder) gekaufte Basis-Menge -> Kaufdruck-Proxy.
    # quote_volume / trades dienen Order-Flow- und Liquiditäts-Auswertungen.
    num_cols = ("open", "high", "low", "close", "volume", "quote_volume",
                "trades", "taker_base", "taker_quote")
    for col in num_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df[[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_base", "taker_quote",
    ]]


if __name__ == "__main__":
    # Kleiner Selbsttest
    print("Ping:", ping())
    syms = get_top_symbols(10)
    print("Top 10:", syms)
    df = get_klines(syms[0], "15m", 5)
    print(df.tail())
