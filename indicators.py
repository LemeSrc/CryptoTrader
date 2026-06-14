"""
Technische Indikatoren und Candlestick-Pattern.

Alle Funktionen arbeiten auf einem DataFrame mit den Spalten
open / high / low / close / volume (wie von binance_client.get_klines geliefert)
und sind in reinem pandas/numpy umgesetzt -> keine TA-Lib/C-Abhängigkeit nötig.

`compute_indicators(df)` reichert das DataFrame um alle Indikator-Spalten an.
"""

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Basis-Bausteine
# ---------------------------------------------------------------------------
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def sma(series, period):
    return series.rolling(period).mean()


def rsi(series, period=14):
    """Relative Strength Index nach Wilder."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder-Glättung = EMA mit alpha = 1/period
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)  # neutral, wenn noch keine Daten


def macd(series, fast=12, slow=26, signal=9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger(series, period=20, std=2.0):
    mid = sma(series, period)
    sd = series.rolling(period).std()
    upper = mid + std * sd
    lower = mid - std * sd
    return upper, mid, lower


def stochastic(df, k_period=14, d_period=3):
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    k = k.fillna(50)
    d = k.rolling(d_period).mean()
    return k, d


def true_range(df):
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(df, period=14):
    return true_range(df).ewm(alpha=1 / period, adjust=False).mean()


def adx(df, period=14):
    """Average Directional Index -> Trendstärke (0..100)."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df)
    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_ = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx_.fillna(0), plus_di.fillna(0), minus_di.fillna(0)


def typical_price(df):
    return (df["high"] + df["low"] + df["close"]) / 3.0


def vwap(df):
    """Volumengewichteter Durchschnittspreis, über das Fenster kumuliert."""
    tp = typical_price(df)
    vol = df["volume"].replace(0, np.nan)
    cum_vol = vol.cumsum()
    cum_pv = (tp * vol).cumsum()
    return (cum_pv / cum_vol).fillna(tp)


def roc(series, period=10):
    """Rate of Change (Momentum) in Prozent."""
    return series.pct_change(period) * 100.0


def obv(df):
    """On-Balance-Volume: kumuliertes Volumen je nach Schlusskurs-Richtung."""
    direction = np.sign(df["close"].diff().fillna(0))
    return (direction * df["volume"]).cumsum()


def donchian(df, period=20):
    """Donchian-Kanal (Breakout): Hoch/Tief der letzten `period` Candles,
    um 1 versetzt, damit die aktuelle Candle den Ausbruch auslösen kann."""
    dh = df["high"].rolling(period).max().shift(1)
    dl = df["low"].rolling(period).min().shift(1)
    return dh, dl


def keltner(df, period=20, mult=1.5):
    mid = ema(df["close"], period)
    rng = atr(df, period) * mult
    return mid + rng, mid, mid - rng


def heikin_ashi(df):
    """Heikin-Ashi-Kerzen (geglätteter Trend). Gibt ha_open, ha_close zurück."""
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
    ha_open = ha_close.copy()
    o = df["open"].to_numpy()
    hc = ha_close.to_numpy()
    ho = np.empty(len(df))
    ho[0] = (o[0] + df["close"].iloc[0]) / 2.0
    for i in range(1, len(df)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2.0
    ha_open[:] = ho
    return ha_open, ha_close


def ichimoku(df, conv=9, base=26, span_b=52):
    """Ichimoku-Wolke. Liefert tenkan, kijun und die auf 'jetzt' projizierten
    Senkou-Span-A/B (also um `base` Perioden nach hinten verschoben), damit der
    aktuelle Kurs direkt mit der Wolke verglichen werden kann."""
    def mid(p):
        return (df["high"].rolling(p).max() + df["low"].rolling(p).min()) / 2.0
    tenkan = mid(conv)
    kijun = mid(base)
    senkou_a = ((tenkan + kijun) / 2.0).shift(base)
    senkou_b = mid(span_b).shift(base)
    return tenkan, kijun, senkou_a, senkou_b


# ---------------------------------------------------------------------------
# Candlestick-Pattern (geben Series mit True/False zurück)
# ---------------------------------------------------------------------------
def _body(df):
    return (df["close"] - df["open"]).abs()


def bullish_engulfing(df):
    prev_red = df["close"].shift(1) < df["open"].shift(1)
    cur_green = df["close"] > df["open"]
    engulf = (df["close"] >= df["open"].shift(1)) & (df["open"] <= df["close"].shift(1))
    return (prev_red & cur_green & engulf).fillna(False)


def bearish_engulfing(df):
    prev_green = df["close"].shift(1) > df["open"].shift(1)
    cur_red = df["close"] < df["open"]
    engulf = (df["open"] >= df["close"].shift(1)) & (df["close"] <= df["open"].shift(1))
    return (prev_green & cur_red & engulf).fillna(False)


def hammer(df):
    body = _body(df)
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return ((lower_wick >= 2 * body) & (upper_wick <= body) & (body / rng < 0.4)).fillna(False)


def shooting_star(df):
    body = _body(df)
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return ((upper_wick >= 2 * body) & (lower_wick <= body) & (body / rng < 0.4)).fillna(False)


def doji(df):
    body = _body(df)
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    return ((body / rng) < 0.1).fillna(False)


def inside_bar(df):
    """Inside Bar: Hoch/Tief liegen komplett innerhalb der Vorkerze (Kompression)."""
    return ((df["high"] < df["high"].shift(1)) &
            (df["low"] > df["low"].shift(1))).fillna(False)


# ---------------------------------------------------------------------------
# Skalar-Helfer: einzelne Kennzahlen aus dem (angereicherten) DataFrame.
# Werden in der Strategie auf der zuletzt geschlossenen Candle (Index -2)
# ausgewertet -> günstiger als volle Spalten und gut für Swing-/Level-Logik.
# ---------------------------------------------------------------------------
def pivot_levels(df, idx=-3):
    """Klassische Pivot-Punkte aus der Candle bei `idx` (Default: die Periode
    VOR der zuletzt geschlossenen). Liefert Pivot, R1/R2 und S1/S2."""
    h = float(df["high"].iloc[idx])
    l = float(df["low"].iloc[idx])
    c = float(df["close"].iloc[idx])
    p = (h + l + c) / 3.0
    return {
        "pivot": p,
        "r1": 2 * p - l, "s1": 2 * p - h,
        "r2": p + (h - l), "s2": p - (h - l),
    }


def swing_fib(df, lookback=50, end=-2):
    """Fibonacci-Retracement über das jüngste Swing-Hoch/-Tief im Lookback.
    Liefert Swing-Hoch/-Tief, Trendrichtung des Swings und die Level 0.382/0.5/0.618."""
    window = df.iloc[max(0, end - lookback):end + 1]
    if window.empty:
        return None
    hi = float(window["high"].max())
    lo = float(window["low"].min())
    rng = hi - lo
    if rng <= 0:
        return None
    # Richtung: kam das Hoch nach dem Tief -> Aufwärts-Swing (Retracements von oben)
    up = window["high"].idxmax() >= window["low"].idxmin()
    if up:
        levels = {"fib_382": hi - 0.382 * rng,
                  "fib_50": hi - 0.5 * rng,
                  "fib_618": hi - 0.618 * rng}
    else:
        levels = {"fib_382": lo + 0.382 * rng,
                  "fib_50": lo + 0.5 * rng,
                  "fib_618": lo + 0.618 * rng}
    return {"swing_high": hi, "swing_low": lo, "swing_up": bool(up), **levels}


def volume_profile_poc(df, lookback=120, bins=24, end=-2):
    """Point of Control: das Preisniveau mit dem höchsten gehandelten Volumen
    im Lookback (Volume-Profile-Näherung aus Klines)."""
    window = df.iloc[max(0, end - lookback):end + 1]
    if len(window) < 10:
        return None
    prices = typical_price(window).to_numpy()
    vols = window["volume"].to_numpy()
    lo, hi = prices.min(), prices.max()
    if hi <= lo:
        return None
    hist, edges = np.histogram(prices, bins=bins, range=(lo, hi), weights=vols)
    i = int(hist.argmax())
    return float((edges[i] + edges[i + 1]) / 2.0)


def opening_range(df, minutes_col="open_time", end=-2):
    """Opening-Range des laufenden UTC-Tages (erste Stunde): Hoch/Tief.
    Für 24/7-Krypto gibt es keine Börseneröffnung -> wir nutzen 00:00 UTC."""
    if minutes_col not in df.columns:
        return None
    sub = df.iloc[:end + 1]
    last_day = sub[minutes_col].iloc[-1].normalize()
    day = sub[sub[minutes_col] >= last_day]
    if day.empty:
        return None
    first_hour = day[day[minutes_col] < last_day + pd.Timedelta(hours=1)]
    if first_hour.empty:
        return None
    return {"or_high": float(first_hour["high"].max()),
            "or_low": float(first_hour["low"].min())}


def macd_divergence(df, lookback=40, end=-2):
    """Erkennt einfache MACD-Divergenzen auf dem Tail:
    'bull' = tieferes Kurstief, aber höheres MACD-Tief; 'bear' = umgekehrt."""
    if "macd" not in df.columns:
        return None
    w = df.iloc[max(0, end - lookback):end + 1]
    if len(w) < 10:
        return None
    half = len(w) // 2
    a, b = w.iloc[:half], w.iloc[half:]
    price_low_a, price_low_b = a["low"].min(), b["low"].min()
    macd_low_a, macd_low_b = a["macd"].min(), b["macd"].min()
    price_high_a, price_high_b = a["high"].max(), b["high"].max()
    macd_high_a, macd_high_b = a["macd"].max(), b["macd"].max()
    if price_low_b < price_low_a and macd_low_b > macd_low_a:
        return "bull"
    if price_high_b > price_high_a and macd_high_b < macd_high_a:
        return "bear"
    return None


# ---------------------------------------------------------------------------
# Sammel-Funktion
# ---------------------------------------------------------------------------
def compute_indicators(df, atr_period=14):
    """Reichert ein Kline-DataFrame um alle Indikator-Spalten an.

    Gibt eine Kopie zurück, das Original bleibt unverändert.
    """
    if df is None or len(df) < 30:
        return None
    df = df.copy()
    close = df["close"]

    df["ema9"] = ema(close, 9)
    df["ema21"] = ema(close, 21)
    df["ema50"] = ema(close, 50)
    df["ema200"] = ema(close, 200) if len(df) >= 200 else ema(close, min(len(df), 100))
    df["sma20"] = sma(close, 20)

    df["rsi"] = rsi(close, 14)
    macd_line, signal_line, hist = macd(close)
    df["macd"] = macd_line
    df["macd_signal"] = signal_line
    df["macd_hist"] = hist

    upper, mid, lower = bollinger(close)
    df["bb_upper"] = upper
    df["bb_mid"] = mid
    df["bb_lower"] = lower

    k, d = stochastic(df)
    df["stoch_k"] = k
    df["stoch_d"] = d

    df["atr"] = atr(df, atr_period)
    adx_, plus_di, minus_di = adx(df)
    df["adx"] = adx_
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    df["vol_sma20"] = sma(df["volume"], 20)
    vol_std = df["volume"].rolling(20).std()
    df["vol_z"] = ((df["volume"] - df["vol_sma20"]) / vol_std.replace(0, np.nan)).fillna(0)

    # --- Momentum / Volumen-Fluss ---
    df["roc"] = roc(close, 10)
    df["vwap"] = vwap(df)
    df["obv"] = obv(df)
    # Order-Flow-Proxy: Anteil des Taker-Kaufvolumens (>0.5 = Kaufdruck).
    if "taker_base" in df.columns:
        df["taker_buy_ratio"] = (df["taker_base"] / df["volume"].replace(0, np.nan)).fillna(0.5)
    else:
        df["taker_buy_ratio"] = 0.5

    # --- Breakout / Squeeze ---
    dh, dl = donchian(df, 20)
    df["donch_high"] = dh
    df["donch_low"] = dl
    kc_u, _kc_m, kc_l = keltner(df, 20, 1.5)
    df["kc_upper"] = kc_u
    df["kc_lower"] = kc_l
    df["bb_width"] = ((df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].replace(0, np.nan)).fillna(0)
    df["bb_squeeze"] = ((df["bb_upper"] < df["kc_upper"]) & (df["bb_lower"] > df["kc_lower"])).fillna(False)

    # --- Heikin-Ashi-Trend ---
    ha_o, ha_c = heikin_ashi(df)
    df["ha_open"] = ha_o
    df["ha_close"] = ha_c
    df["ha_bull"] = ha_c > ha_o

    # --- Ichimoku-Wolke (auf 'jetzt' projiziert) ---
    tenkan, kijun, span_a, span_b = ichimoku(df)
    df["tenkan"] = tenkan
    df["kijun"] = kijun
    df["senkou_a"] = span_a
    df["senkou_b"] = span_b
    df["cloud_top"] = pd.concat([span_a, span_b], axis=1).max(axis=1)
    df["cloud_bottom"] = pd.concat([span_a, span_b], axis=1).min(axis=1)

    # Candlestick-Pattern
    df["pat_bull_engulf"] = bullish_engulfing(df)
    df["pat_bear_engulf"] = bearish_engulfing(df)
    df["pat_hammer"] = hammer(df)
    df["pat_shooting_star"] = shooting_star(df)
    df["pat_doji"] = doji(df)
    df["pat_inside_bar"] = inside_bar(df)

    return df
