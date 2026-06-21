"""
Anbindung an die öffentliche Binance-API.

Standard ist die USDT-M **Futures**-API (fapi) — passend zum Perp-/Hebel-Sim
des Volume-Bots: echte Perp-Preise, Futures-Taker-Volumen (Order-Flow) und die
reale **Funding-Rate** fürs Kostenmodell. Es werden ausschließlich öffentliche
Endpunkte verwendet -> KEIN API-Key nötig, kein Risiko.

Optionaler Fallback auf Spot (config.USE_FUTURES = False bzw. CT_USE_FUTURES=0):
gleiche Funktionen, dann ohne Funding (Default-Rate) und mit Spot-Preisen.

Geliefert werden:
  * die Top-Coins nach 24h-Quote-Volumen (liquideste Perps)
  * Candlestick-Daten (Klines) als pandas DataFrame (inkl. Taker-Buy-Volumen)
  * die aktuelle Funding-Rate je Symbol (nur Futures)
"""

import time
import requests
import pandas as pd

import config

# --- Host-/Pfad-Auswahl je nach Markt (Futures vs. Spot) -------------------
# Futures (USDT-M): fapi.binance.com. Klines haben dasselbe 12-Spalten-Format
# wie Spot, daher funktioniert das Parsing für beide Märkte identisch.
_FUTURES_HOSTS = ["https://fapi.binance.com"]
_SPOT_HOSTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api-gcp.binance.com",
    "https://data-api.binance.vision",  # reiner Markt-Daten-Host
]


def _use_futures():
    return getattr(config, "USE_FUTURES", True)


def _hosts():
    return _FUTURES_HOSTS if _use_futures() else _SPOT_HOSTS


def _path(kind):
    """Endpunkt-Pfad je Markt. kind in {klines, ticker24, funding}."""
    if _use_futures():
        return {
            "klines": "/fapi/v1/klines",
            "ticker24": "/fapi/v1/ticker/24hr",
            "funding": "/fapi/v1/premiumIndex",
            "ping": "/fapi/v1/ping",
        }[kind]
    return {
        "klines": "/api/v3/klines",
        "ticker24": "/api/v3/ticker/24hr",
        "funding": None,            # Spot kennt kein Funding
        "ping": "/api/v3/ping",
    }[kind]


_session = requests.Session()
_session.headers.update({"User-Agent": "CryptoTraderVolumeBot/2.0"})


def _get(path, params=None, retries=3):
    """GET-Request mit Host-Fallback und einfachem Retry."""
    last_err = None
    for host in _hosts():
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
        _get(_path("ping"))
        return True
    except RuntimeError:
        return False


def _symbol_ok(sym, quote):
    """Filtert auf saubere Perp-/Spot-Paare gegen die Quote-Währung."""
    if not sym.endswith(quote):
        return False
    if "_" in sym:                       # Delivery-/Quartals-Kontrakte (z.B. BTCUSDT_240329)
        return False
    if sym in config.SYMBOL_BLACKLIST:
        return False
    if any(sym.endswith(suf) for suf in config.SYMBOL_EXCLUDE_SUFFIXES):
        return False
    return True


def get_top_symbols(n=None, quote=None):
    """Liefert die n liquidesten Handelspaare gegen die Quote-Währung.

    Sortiert nach 24h-Quote-Volumen (tatsächlicher USDT-Umsatz) -> liquideste
    Coins zuerst (enger Spread, weniger Slippage).
    """
    n = n or config.BASE_UNIVERSE_N
    quote = quote or config.QUOTE_ASSET
    data = _get(_path("ticker24"))

    rows = []
    for t in data:
        sym = t.get("symbol", "")
        if not _symbol_ok(sym, quote):
            continue
        try:
            qvol = float(t["quoteVolume"])
        except (KeyError, ValueError, TypeError):
            continue
        if qvol <= 0:
            continue
        rows.append((sym, qvol))

    rows.sort(key=lambda x: x[1], reverse=True)
    return [sym for sym, _ in rows[:n]]


def get_funding_rates():
    """Aktuelle Funding-Rate je Symbol als dict {symbol: rate_pro_8h}.

    Nur Futures. Im Spot-Modus (oder bei Fehler) leeres dict -> der Bot nutzt
    dann die Default-Funding-Rate aus der Config.
    """
    path = _path("funding")
    if path is None:
        return {}
    try:
        data = _get(path)
    except RuntimeError:
        return {}
    out = {}
    # /fapi/v1/premiumIndex ohne Symbol -> Liste aller Symbole
    items = data if isinstance(data, list) else [data]
    for t in items:
        sym = t.get("symbol")
        try:
            out[sym] = float(t["lastFundingRate"])
        except (KeyError, ValueError, TypeError):
            continue
    return out


def get_klines(symbol, interval, limit=None):
    """Holt Candlestick-Daten und gibt sie als DataFrame zurück.

    Spalten: open_time (UTC), open, high, low, close, volume, close_time,
    quote_volume, trades, taker_base, taker_quote.
    """
    limit = limit or config.KLINE_LIMIT
    raw = _get(
        _path("klines"),
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
    # taker_base = vom Taker (Marktorder) gekaufte Basis-Menge -> Kaufdruck/Delta.
    # quote_volume / trades dienen Order-Flow-, Liquiditäts- und Slippage-Schätzung.
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
    print("Markt:", "FUTURES" if _use_futures() else "SPOT")
    print("Ping:", ping())
    syms = get_top_symbols(10)
    print("Top 10:", syms)
    fr = get_funding_rates()
    if syms and fr:
        print(f"Funding {syms[0]}: {fr.get(syms[0])}")
    df = get_klines(syms[0], "1m", 5)
    print(df.tail())
