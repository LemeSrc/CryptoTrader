"""
VOLUMEN-Indikatoren (und nur diese).

Der Bot ist auf Volumen spezialisiert. Hier leben ausschließlich Volume
Profile, Order Flow (CVD/Delta) und volumennahe Kennzahlen (RVOL, VWAP, MFI,
OBV) plus ATR als reines Volatilitätsmaß für Stop-Abstände/Slippage — KEINE
klassischen Trend-/Oszillator-Indikatoren mehr.

Alle Funktionen arbeiten auf einem DataFrame mit den Spalten
open/high/low/close/volume/quote_volume/trades/taker_base
(wie von binance_client.get_klines geliefert), in reinem pandas/numpy.

`compute_indicators(df)` reichert das DataFrame um alle Spalten an.
`volume_profile(df)` liefert POC/VAH/VAL + Volumen-Nodes für Ziele/Stops.
"""

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Basis
# ---------------------------------------------------------------------------
def sma(series, period):
    return series.rolling(period).mean()


def typical_price(df):
    return (df["high"] + df["low"] + df["close"]) / 3.0


def true_range(df):
    prev_close = df["close"].shift(1)
    return pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(df, period=14):
    """Average True Range — nur als Volatilitätsmaß (Stop-Abstand/Slippage)."""
    return true_range(df).ewm(alpha=1 / period, adjust=False).mean()


# ---------------------------------------------------------------------------
# Order Flow / Delta / CVD
# ---------------------------------------------------------------------------
def order_flow(df):
    """Order-Flow-Kennzahlen aus dem Taker-Buy-Volumen.

    delta            = aggressives Kauf- minus Verkaufsvolumen je Candle
                       = taker_base - (volume - taker_base) = 2*taker_base - volume
    taker_buy_ratio  = Anteil des aggressiven Kaufvolumens (>0,5 = Kaufdruck)
    cvd              = kumulatives Delta über das Fenster (Cumulative Volume Delta)
    """
    if "taker_base" in df.columns:
        taker = df["taker_base"].fillna(df["volume"] / 2.0)
    else:
        taker = df["volume"] / 2.0
    delta = 2.0 * taker - df["volume"]
    ratio = (taker / df["volume"].replace(0, np.nan)).fillna(0.5).clip(0, 1)
    cvd = delta.cumsum()
    return delta, ratio, cvd


def money_flow_index(df, period=14):
    """Money Flow Index: volumengewichteter RSI (0..100). >80 überkauft,
    <20 überverkauft — rein aus Preis*Volumen (Money Flow)."""
    tp = typical_price(df)
    mf = tp * df["volume"]
    up = tp > tp.shift(1)
    pos = mf.where(up, 0.0).rolling(period).sum()
    neg = mf.where(~up, 0.0).rolling(period).sum()
    mfr = pos / neg.replace(0, np.nan)
    return (100 - 100 / (1 + mfr)).fillna(50)


def obv(df):
    """On-Balance-Volume: kumuliertes Volumen je nach Schlusskurs-Richtung."""
    direction = np.sign(df["close"].diff().fillna(0))
    return (direction * df["volume"]).cumsum()


def vwap(df):
    """Volumengewichteter Durchschnittspreis über das Fenster (kumuliert)."""
    tp = typical_price(df)
    vol = df["volume"].replace(0, np.nan)
    cum_vol = vol.cumsum()
    cum_pv = (tp * vol).cumsum()
    return (cum_pv / cum_vol).fillna(tp)


# ---------------------------------------------------------------------------
# Volume Profile (POC / VAH / VAL / Nodes)
# ---------------------------------------------------------------------------
def volume_profile(df, lookback=240, bins=30, value_area_pct=0.70, end=-2):
    """Volume Profile aus Klines (Näherung über typical-price-Histogramm).

    Liefert dict:
      poc            Point of Control (Preisniveau mit dem meisten Volumen)
      vah / val      Value Area High / Low (enthält value_area_pct des Volumens)
      nodes          Liste (preis, volumen) je Bin, nach Preis sortiert
      hi / lo        Profil-Spanne
    oder None, wenn zu wenig Daten.
    """
    window = df.iloc[max(0, end - lookback):end + 1]
    if len(window) < 20:
        return None
    prices = typical_price(window).to_numpy()
    vols = window["volume"].to_numpy()
    lo, hi = float(prices.min()), float(prices.max())
    if hi <= lo:
        return None

    hist, edges = np.histogram(prices, bins=bins, range=(lo, hi), weights=vols)
    centers = (edges[:-1] + edges[1:]) / 2.0
    total = hist.sum()
    if total <= 0:
        return None

    poc_i = int(hist.argmax())
    poc = float(centers[poc_i])

    # Value Area: vom POC aus nach oben/unten greedy erweitern, bis
    # value_area_pct des Gesamtvolumens abgedeckt sind.
    target = total * value_area_pct
    covered = hist[poc_i]
    lo_i = hi_i = poc_i
    while covered < target and (lo_i > 0 or hi_i < len(hist) - 1):
        below = hist[lo_i - 1] if lo_i > 0 else -1
        above = hist[hi_i + 1] if hi_i < len(hist) - 1 else -1
        if above >= below:
            hi_i += 1
            covered += hist[hi_i]
        else:
            lo_i -= 1
            covered += hist[lo_i]
    val = float(centers[lo_i])
    vah = float(centers[hi_i])

    nodes = [(float(c), float(v)) for c, v in zip(centers, hist)]
    return {"poc": poc, "vah": vah, "val": val, "nodes": nodes,
            "hi": hi, "lo": lo, "total": float(total)}


def next_node(profile, price, direction):
    """Nächstgelegenes Volumen-Node (Bin-Center) in `direction` (+1 hoch / -1 runter)
    von `price` aus — dient als struktur-basiertes Kursziel. None, wenn keins."""
    if profile is None:
        return None
    cands = [c for c, _v in profile["nodes"]
             if (direction > 0 and c > price) or (direction < 0 and c < price)]
    if not cands:
        return None
    return min(cands) if direction > 0 else max(cands)


def structural_levels(profile, price, side, buffer_va_frac=0.15):
    """Stop UND Ziel vollständig aus dem Volume Profile — keine ATR-Durchschnitte.

    Idee: zwischen den Volumen-Niveaus {Profil-Tief, VAL, POC, VAH, Profil-Hoch}
    wird das nächste Level in Handelsrichtung das ZIEL, das nächste Level dagegen
    die Struktur, hinter der (mit kleinem Puffer) der STOP sitzt. Bricht der Kurs
    dieses Volumen-Level, ist die Idee widerlegt — kein Ausstoppen durch Rauschen.

    `buffer_va_frac`: Puffer hinter das Level als Anteil der Value-Area-Höhe
    (struktureller Maßstab, kein Mittelwert). Gibt (stop_level, target_level)
    oder (None, None), wenn kein Profil vorliegt.
    """
    if profile is None:
        return None, None
    lo, val, poc, vah, hi = (profile["lo"], profile["val"], profile["poc"],
                             profile["vah"], profile["hi"])
    # Value-Area-Höhe als struktureller Maßstab (mit robusten Untergrenzen)
    va_h = max(vah - val, (hi - lo) * 0.3, price * 0.005)
    buf = va_h * buffer_va_frac
    levels = sorted({lo, val, poc, vah, hi})
    above = [l for l in levels if l > price + 1e-12]
    below = [l for l in levels if l < price - 1e-12]

    if side == "long":
        target = min(above) if above else price + va_h          # nächstes Volumen-Level / Projektion
        support = max(below) if below else (price - va_h)       # Struktur, die long widerlegt
        stop = support - buf
    else:
        target = max(below) if below else price - va_h
        resist = min(above) if above else (price + va_h)
        stop = resist + buf
    return stop, target


def delta_divergence(df, lookback=40, end=-2):
    """Order-Flow-Divergenz zwischen Preis und CVD auf dem Tail.
    'bull' = tieferes Kurstief, aber höheres CVD-Tief (versteckter Kaufdruck);
    'bear' = höheres Kurshoch, aber tieferes CVD-Hoch. Sonst None.
    Erwartet die Spalte 'cvd' (aus compute_indicators)."""
    if "cvd" not in df.columns:
        return None
    w = df.iloc[max(0, end - lookback):end + 1]
    if len(w) < 12:
        return None
    half = len(w) // 2
    a, b = w.iloc[:half], w.iloc[half:]
    if b["low"].min() < a["low"].min() and b["cvd"].min() > a["cvd"].min():
        return "bull"
    if b["high"].max() > a["high"].max() and b["cvd"].max() < a["cvd"].max():
        return "bear"
    return None


# ---------------------------------------------------------------------------
# Sammel-Funktion
# ---------------------------------------------------------------------------
def compute_indicators(df, atr_period=14, vol_period=20, cvd_window=10):
    """Reichert ein Kline-DataFrame um alle Volumen-Spalten an (Kopie)."""
    if df is None or len(df) < 30:
        return None
    df = df.copy()

    df["atr"] = atr(df, atr_period)

    # --- Relatives Volumen (Surge-Erkennung, zentrales Gate) ---
    df["vol_sma"] = sma(df["volume"], vol_period)
    df["rvol"] = (df["volume"] / df["vol_sma"].replace(0, np.nan)).fillna(0)

    # --- Order Flow / Delta / CVD ---
    delta, ratio, cvd = order_flow(df)
    df["delta"] = delta
    df["taker_buy_ratio"] = ratio
    df["cvd"] = cvd
    # Netto-Delta der letzten cvd_window Candles (jüngster Order-Flow-Druck)
    df["cvd_roc"] = cvd - cvd.shift(cvd_window)
    df["delta_sma"] = sma(delta, vol_period)

    # --- VWAP + Abstand in ATR ---
    df["vwap"] = vwap(df)
    df["vwap_dist_atr"] = ((df["close"] - df["vwap"])
                           / df["atr"].replace(0, np.nan)).fillna(0)

    # --- Money Flow Index + OBV ---
    df["mfi"] = money_flow_index(df, 14)
    df["obv"] = obv(df)
    df["obv_slope"] = df["obv"] - df["obv"].shift(cvd_window)

    # --- Zusatz-Features (für die datengetriebene Auswertung der Exporte) ---
    # Normierte Größen sind über Coins hinweg vergleichbar -> besseres Lernmaterial.
    df["atr_pct"] = (df["atr"] / df["close"].replace(0, np.nan) * 100).fillna(0)
    df["delta_ratio"] = (df["delta"] / df["volume"].replace(0, np.nan)).fillna(0)
    df["cvd_roc_norm"] = (df["cvd_roc"]
                          / (df["vol_sma"] * cvd_window).replace(0, np.nan)).fillna(0)
    if "quote_volume" in df.columns:
        qsma = sma(df["quote_volume"], vol_period)
        df["rvol_quote"] = (df["quote_volume"] / qsma.replace(0, np.nan)).fillna(0)
    else:
        df["rvol_quote"] = 0.0
    if "trades" in df.columns:
        tsma = sma(df["trades"], vol_period)
        df["trades_rvol"] = (df["trades"] / tsma.replace(0, np.nan)).fillna(0)
        df["avg_trade_size"] = (df["volume"] / df["trades"].replace(0, np.nan)).fillna(0)
    else:
        df["trades_rvol"] = 0.0
        df["avg_trade_size"] = 0.0
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    df["body_ratio"] = ((df["close"] - df["open"]).abs() / rng).fillna(0)
    df["range_pct"] = (rng / df["close"].replace(0, np.nan) * 100).fillna(0)
    df["close_pos_in_range"] = ((df["close"] - df["low"]) / rng).fillna(0.5)
    # Vorzeichenbehafteter Streak gleichgerichteter Schlusskurse (+3 = 3 grüne in Folge)
    direction = np.sign(df["close"].diff().fillna(0)).to_numpy()
    streak = np.zeros(len(df))
    for i in range(1, len(df)):
        if direction[i] != 0 and direction[i] == direction[i - 1]:
            streak[i] = streak[i - 1] + direction[i]
        else:
            streak[i] = direction[i]
    df["updown_streak"] = streak

    return df
