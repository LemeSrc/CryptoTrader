"""
Mehrbalken-Chartmuster (Formationen über viele Candles).

Anders als die Einzelkerzen-Pattern in indicators.py erkennen diese Funktionen
Strukturen, die sich über Dutzende Candles erstrecken. Auf einem 1d-Chart sind
das Formationen über mehrere Wochen bis Monate:

  * Doppeltop / Doppelboden
  * Schulter-Kopf-Schulter (+ invers)
  * aufsteigendes / absteigendes Dreieck
  * Ausbruch aus einer langen Range (mehrwöchig)

Alles arbeitet auf dem OHLC-DataFrame und gibt Treffer als
(side, name, strength) zurück. `side` ist 'long' oder 'short', `strength` ~0.4..1.0.
"""

import numpy as np


def _rel_close(a, b, tol=0.025):
    """True, wenn a und b relativ (Default 2.5 %) nahe beieinander liegen."""
    m = (abs(a) + abs(b)) / 2.0
    return m > 0 and abs(a - b) <= tol * m


def find_swings(df, left=3, right=3, end=-2):
    """Fraktale Swing-Hochs/-Tiefs der abgeschlossenen Candles.

    Ein Swing-Hoch bei i ist höher als die `left` Candles davor und die `right`
    danach. Gibt (highs, lows) als Listen von (index, preis) in zeitlicher
    Reihenfolge zurück. `end` = letzte zu berücksichtigende (abgeschlossene) Candle.
    """
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    n = len(df)
    last = n + 1 + end  # exklusiver Index hinter der letzten abgeschlossenen Candle
    highs, lows = [], []
    for i in range(left, last - right):
        if (h[i] > h[i - left:i].max()) and (h[i] >= h[i + 1:i + right + 1].max()):
            highs.append((i, float(h[i])))
        if (l[i] < l[i - left:i].min()) and (l[i] <= l[i + 1:i + right + 1].min()):
            lows.append((i, float(l[i])))
    return highs, lows


def detect_chart_patterns(df, lookback=90, left=3, right=3, end=-2):
    """Sucht im Lookback-Fenster nach Mehrbalken-Formationen.

    Liefert eine Liste von (side, name, strength). Mehrere Treffer möglich.
    """
    n = len(df)
    if n < lookback + 5:
        lookback = max(20, n - 5)
    start = max(0, n + 1 + end - lookback)
    win = df.iloc[start:n + 1 + end]
    if len(win) < 20:
        return []

    highs, lows = find_swings(win, left, right, end=-1)
    close = float(win["close"].iloc[-1])
    hi_all = float(win["high"].iloc[:-1].max())
    lo_all = float(win["low"].iloc[:-1].min())
    rng = hi_all - lo_all
    if rng <= 0:
        return []

    out = []

    # === Range-Breakout (mehrwöchig): Ausbruch aus langer Seitwärtsspanne ===
    # Spanne der vorangegangenen Candles; aktuelle Candle bricht heraus.
    prior_high = float(win["high"].iloc[:-1].max())
    prior_low = float(win["low"].iloc[:-1].min())
    if close > prior_high:
        out.append(("long", "Range-Breakout (hoch)", 1.0))
    elif close < prior_low:
        out.append(("short", "Range-Breakout (tief)", 1.0))

    # === Doppeltop / Doppelboden ===
    if len(highs) >= 2:
        (i1, p1), (i2, p2) = highs[-2], highs[-1]
        # Zwischentief zwischen den beiden Hochs (Nackenlinie)
        mids = [pl for (idx, pl) in lows if i1 < idx < i2]
        if _rel_close(p1, p2, 0.03) and mids:
            neckline = min(mids)
            broken = close < neckline
            out.append(("short", "Doppeltop", 1.0 if broken else 0.5))
    if len(lows) >= 2:
        (i1, p1), (i2, p2) = lows[-2], lows[-1]
        mids = [ph for (idx, ph) in highs if i1 < idx < i2]
        if _rel_close(p1, p2, 0.03) and mids:
            neckline = max(mids)
            broken = close > neckline
            out.append(("long", "Doppelboden", 1.0 if broken else 0.5))

    # === Schulter-Kopf-Schulter (bärisch) ===
    if len(highs) >= 3:
        (iL, L), (iH, H), (iR, R) = highs[-3], highs[-2], highs[-1]
        if H > L and H > R and _rel_close(L, R, 0.04):
            necks = [pl for (idx, pl) in lows if iL < idx < iR]
            if necks:
                neckline = float(np.mean(necks))
                broken = close < neckline
                out.append(("short", "Schulter-Kopf-Schulter", 1.0 if broken else 0.5))
    # === Inverse SKS (bullisch) ===
    if len(lows) >= 3:
        (iL, L), (iH, H), (iR, R) = lows[-3], lows[-2], lows[-1]
        if H < L and H < R and _rel_close(L, R, 0.04):
            necks = [ph for (idx, ph) in highs if iL < idx < iR]
            if necks:
                neckline = float(np.mean(necks))
                broken = close > neckline
                out.append(("long", "Inverse Schulter-Kopf-Schulter", 1.0 if broken else 0.5))

    # === Dreiecke (Trendlinien der Swings) ===
    if len(highs) >= 2 and len(lows) >= 2:
        (hi_a, hpa), (hi_b, hpb) = highs[0], highs[-1]
        (lo_a, lpa), (lo_b, lpb) = lows[0], lows[-1]
        # Steigung relativ zur Spanne (pro Candle), klein = "flach"
        h_slope = (hpb - hpa) / max(1, hi_b - hi_a) / rng
        l_slope = (lpb - lpa) / max(1, lo_b - lo_a) / rng
        flat = 0.002
        if abs(h_slope) < flat and l_slope > flat:
            out.append(("long", "Aufsteigendes Dreieck", 0.7))
        elif abs(l_slope) < flat and h_slope < -flat:
            out.append(("short", "Absteigendes Dreieck", 0.7))

    return out
