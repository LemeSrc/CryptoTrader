"""
Signal-Logik (Strategie).

Kombiniert mehrere Indikatoren über mehrere Zeitrahmen zu einem Long-/Short-
Score. Jede erfüllte Einzelbedingung gibt Punkte. Liegt der Score über der
(bewusst niedrigen) Schwelle in config.ENTRY_SCORE_THRESHOLD, entsteht ein
Signal. Zusätzlich wird ein vollständiger Indikator-"Snapshot" erzeugt, der
mit jedem Trade gespeichert wird -> Grundlage für die spätere Analyse.
"""

import numpy as np

import config
from indicators import (
    compute_indicators, pivot_levels, swing_fib, volume_profile_poc,
    opening_range, macd_divergence,
)
from patterns import detect_chart_patterns


def _last_two(df):
    """Gibt die letzte abgeschlossene und die vorletzte Candle-Zeile zurück.

    Die allerletzte Candle ist noch nicht abgeschlossen, daher nutzen wir
    index -2 (abgeschlossen) und -3 (davor) für Cross-Vergleiche.
    """
    return df.iloc[-2], df.iloc[-3]


def _near(price, level, atr_val, k=0.3):
    """True, wenn `price` höchstens k*ATR von `level` entfernt ist."""
    if level is None or atr_val is None or atr_val <= 0:
        return False
    return abs(price - level) <= k * atr_val


def _evaluate_timeframe(df, tf):
    """Bewertet einen Zeitrahmen über alle benannten Strategien.

    Gibt eine Liste von Beiträgen (side, strategy, base_points, text) zurück.
    Die Gewichtung (Zeitrahmen + Strategie) passiert zentral in `analyze`.
    """
    cur, prev = _last_two(df)
    out = []

    def c(side, strategy, pts, text):
        out.append((side, strategy, pts, text))

    price = float(cur["close"])
    atr_val = float(cur["atr"]) if cur["atr"] == cur["atr"] else 0.0  # NaN-Schutz

    # === Trend Following: ADX-Richtung + Lage zur EMA200 ===
    if cur["adx"] > 20 and cur["plus_di"] > cur["minus_di"] and price > cur["ema200"]:
        c("long", "Trend Following", 1.0, f"Aufwärtstrend (ADX {cur['adx']:.0f}, +DI)")
    elif cur["adx"] > 20 and cur["minus_di"] > cur["plus_di"] and price < cur["ema200"]:
        c("short", "Trend Following", 1.0, f"Abwärtstrend (ADX {cur['adx']:.0f}, -DI)")
    else:
        c("long" if price > cur["ema200"] else "short", "Trend Following", 0.3,
          "Preis " + (">" if price > cur["ema200"] else "<") + " EMA200")

    # === Moving Average Cross: EMA9/EMA21 + MACD-Kreuz ===
    if prev["ema9"] <= prev["ema21"] and cur["ema9"] > cur["ema21"]:
        c("long", "Moving Average Cross", 1.0, "EMA9 kreuzt über EMA21")
    elif prev["ema9"] >= prev["ema21"] and cur["ema9"] < cur["ema21"]:
        c("short", "Moving Average Cross", 1.0, "EMA9 kreuzt unter EMA21")
    else:
        c("long" if cur["ema9"] > cur["ema21"] else "short", "Moving Average Cross",
          0.4, "EMA9 " + (">" if cur["ema9"] > cur["ema21"] else "<") + " EMA21")
    if prev["macd"] <= prev["macd_signal"] and cur["macd"] > cur["macd_signal"]:
        c("long", "Moving Average Cross", 0.8, "MACD bullisches Kreuz")
    if prev["macd"] >= prev["macd_signal"] and cur["macd"] < cur["macd_signal"]:
        c("short", "Moving Average Cross", 0.8, "MACD bärisches Kreuz")

    # === Momentum: ROC + MACD-Histogramm ===
    if cur["roc"] > 0 and cur["roc"] > prev["roc"]:
        c("long", "Momentum", 0.8, f"ROC steigt ({cur['roc']:.1f}%)")
    if cur["roc"] < 0 and cur["roc"] < prev["roc"]:
        c("short", "Momentum", 0.8, f"ROC fällt ({cur['roc']:.1f}%)")
    if cur["macd_hist"] > 0 and cur["macd_hist"] > prev["macd_hist"]:
        c("long", "Momentum", 0.5, "MACD-Histogramm steigt")
    if cur["macd_hist"] < 0 and cur["macd_hist"] < prev["macd_hist"]:
        c("short", "Momentum", 0.5, "MACD-Histogramm fällt")

    # === Mean Reversion: RSI-Extrem + Bollinger-Rand ===
    if cur["rsi"] < 30:
        c("long", "Mean Reversion", 1.0, f"RSI überverkauft ({cur['rsi']:.0f})")
    elif cur["rsi"] > 70:
        c("short", "Mean Reversion", 1.0, f"RSI überkauft ({cur['rsi']:.0f})")
    if prev["rsi"] < 30 <= cur["rsi"]:
        c("long", "Mean Reversion", 0.6, "RSI kreuzt über 30")
    if prev["rsi"] > 70 >= cur["rsi"]:
        c("short", "Mean Reversion", 0.6, "RSI kreuzt unter 70")
    if price <= cur["bb_lower"]:
        c("long", "Mean Reversion", 0.8, "Preis am unteren Bollinger-Band")
    if price >= cur["bb_upper"]:
        c("short", "Mean Reversion", 0.8, "Preis am oberen Bollinger-Band")

    # === Breakout: Donchian-Ausbruch ===
    if cur["donch_high"] == cur["donch_high"] and price > cur["donch_high"]:
        c("long", "Breakout", 1.0, "Ausbruch über 20-Perioden-Hoch")
    if cur["donch_low"] == cur["donch_low"] and price < cur["donch_low"]:
        c("short", "Breakout", 1.0, "Ausbruch unter 20-Perioden-Tief")

    # === Bollinger Squeeze: enge Bänder lösen sich auf ===
    if bool(prev["bb_squeeze"]) and not bool(cur["bb_squeeze"]):
        if price > cur["bb_mid"]:
            c("long", "Bollinger Squeeze", 1.0, "Squeeze löst sich nach oben")
        else:
            c("short", "Bollinger Squeeze", 1.0, "Squeeze löst sich nach unten")

    # === Range: kein Trend (ADX<20) -> Extreme faden ===
    if cur["adx"] < 20:
        if _near(price, cur["bb_lower"], atr_val, 0.4):
            c("long", "Range", 0.8, "Range: Kauf am unteren Rand")
        if _near(price, cur["bb_upper"], atr_val, 0.4):
            c("short", "Range", 0.8, "Range: Verkauf am oberen Rand")

    # === Reversal: Stochastik-Kreuz im Extrem + Umkehrkerzen ===
    if cur["stoch_k"] < 25 and prev["stoch_k"] <= prev["stoch_d"] and cur["stoch_k"] > cur["stoch_d"]:
        c("long", "Reversal", 1.0, "Stochastik bullisches Kreuz (überverkauft)")
    if cur["stoch_k"] > 75 and prev["stoch_k"] >= prev["stoch_d"] and cur["stoch_k"] < cur["stoch_d"]:
        c("short", "Reversal", 1.0, "Stochastik bärisches Kreuz (überkauft)")
    if bool(cur["pat_hammer"]):
        c("long", "Reversal", 0.7, "Hammer-Kerze")
    if bool(cur["pat_shooting_star"]):
        c("short", "Reversal", 0.7, "Shooting-Star-Kerze")

    # === Market Fluctuation: wie stark wird ein Preis abgelehnt? (Docht/Lunte) ===
    # Lange untere Lunte = der Markt lehnt tiefere Preise ab (Käufer drücken hoch)
    #   -> long. Lange obere Lunte = Ablehnung höherer Preise -> short. In stark
    # oszillierenden (choppy) Phasen wird das Signal verstärkt. Durch das 4h-Trend-
    # Gate werden daraus automatisch Pullback-Einstiege MIT dem Trend.
    lwr = float(cur["lower_wick_ratio"])
    uwr = float(cur["upper_wick_ratio"])
    chop_boost = 1.3 if float(cur["chop"]) > 55 else 1.0
    near_low = price <= cur["bb_lower"] or _near(price, cur["bb_lower"], atr_val, 0.6)
    near_high = price >= cur["bb_upper"] or _near(price, cur["bb_upper"], atr_val, 0.6)
    if lwr >= 0.5 and (near_low or cur["rsi"] < 45):
        c("long", "Market Fluctuation", 0.9 * chop_boost,
          f"Ablehnung tiefer Preise (Lunte {lwr*100:.0f}%)")
    if uwr >= 0.5 and (near_high or cur["rsi"] > 55):
        c("short", "Market Fluctuation", 0.9 * chop_boost,
          f"Ablehnung hoher Preise (Docht {uwr*100:.0f}%)")

    # === VWAP: Lage + Re-Cross ===
    if cur["vwap"] == cur["vwap"]:
        c("long" if price > cur["vwap"] else "short", "VWAP", 0.4,
          "Preis " + (">" if price > cur["vwap"] else "<") + " VWAP")
        if prev["close"] <= prev["vwap"] and price > cur["vwap"]:
            c("long", "VWAP", 0.6, "VWAP-Cross nach oben")
        if prev["close"] >= prev["vwap"] and price < cur["vwap"]:
            c("short", "VWAP", 0.6, "VWAP-Cross nach unten")

    # === Volume Profile: Lage zum Point of Control ===
    poc = volume_profile_poc(df)
    if poc is not None:
        c("long" if price > poc else "short", "Volume Profile", 0.4,
          "Preis " + (">" if price > poc else "<") + " POC")

    # === Pivot Points: Lage zu Pivot / Bounce an S1/R1 ===
    piv = pivot_levels(df)
    c("long" if price > piv["pivot"] else "short", "Pivot Points", 0.4,
      "Preis " + (">" if price > piv["pivot"] else "<") + " Pivot")
    if _near(price, piv["s1"], atr_val, 0.3) and price >= piv["s1"]:
        c("long", "Pivot Points", 0.6, "Abpraller an S1")
    if _near(price, piv["r1"], atr_val, 0.3) and price <= piv["r1"]:
        c("short", "Pivot Points", 0.6, "Abweisung an R1")

    # === Fibonacci: Retracement in Trendrichtung ===
    fib = swing_fib(df)
    if fib is not None:
        for lvl in ("fib_382", "fib_50", "fib_618"):
            if _near(price, fib[lvl], atr_val, 0.3):
                c("long" if fib["swing_up"] else "short", "Fibonacci", 0.6,
                  f"Retracement am {lvl.replace('fib_', '0.')}-Level")
                break

    # === Heikin Ashi: geglätteter Trend ===
    ha_bull = bool(cur["ha_bull"])
    prev_ha_bull = bool(prev["ha_bull"])
    if ha_bull and prev_ha_bull:
        c("long", "Heikin Ashi", 0.6, "HA-Trend aufwärts")
    elif not ha_bull and not prev_ha_bull:
        c("short", "Heikin Ashi", 0.6, "HA-Trend abwärts")

    # === Ichimoku: Wolke + Tenkan/Kijun ===
    if cur["cloud_top"] == cur["cloud_top"]:
        if price > cur["cloud_top"]:
            c("long", "Ichimoku", 1.0, "Preis über der Wolke")
        elif price < cur["cloud_bottom"]:
            c("short", "Ichimoku", 1.0, "Preis unter der Wolke")
    if cur["tenkan"] == cur["tenkan"] and cur["kijun"] == cur["kijun"]:
        c("long" if cur["tenkan"] > cur["kijun"] else "short", "Ichimoku", 0.4,
          "Tenkan " + (">" if cur["tenkan"] > cur["kijun"] else "<") + " Kijun")

    # === MACD Divergence ===
    div = macd_divergence(df)
    if div == "bull":
        c("long", "MACD Divergence", 1.0, "Bullische MACD-Divergenz")
    elif div == "bear":
        c("short", "MACD Divergence", 1.0, "Bärische MACD-Divergenz")

    # === Opening Range Breakout (nur Haupt-Zeitrahmen) ===
    if tf == config.PRIMARY_TIMEFRAME:
        orr = opening_range(df)
        if orr is not None:
            if price > orr["or_high"]:
                c("long", "Opening Range", 0.8, "Ausbruch über Tages-Opening-Range")
            elif price < orr["or_low"]:
                c("short", "Opening Range", 0.8, "Ausbruch unter Tages-Opening-Range")

    # === Chart-Pattern (mehrtägig/-wöchig, nur höhere Zeitrahmen) ===
    if tf in config.CHART_PATTERN_TFS:
        for pside, pname, strength in detect_chart_patterns(df):
            c(pside, "Chart Patterns", strength, f"{pname} ({tf})")

    # === Price Action: Engulfing + Inside-Bar-Ausbruch ===
    if bool(cur["pat_bull_engulf"]):
        c("long", "Price Action", 0.9, "Bullisches Engulfing")
    if bool(cur["pat_bear_engulf"]):
        c("short", "Price Action", 0.9, "Bärisches Engulfing")
    if bool(prev["pat_inside_bar"]):
        if price > prev["high"]:
            c("long", "Price Action", 0.6, "Inside-Bar-Ausbruch nach oben")
        elif price < prev["low"]:
            c("short", "Price Action", 0.6, "Inside-Bar-Ausbruch nach unten")

    # === Order Flow (Proxy): Taker-Kaufvolumen-Anteil ===
    tbr = float(cur["taker_buy_ratio"])
    if tbr > 0.58:
        c("long", "Order Flow (approx)", 0.6, f"Kaufdruck (Taker-Buy {tbr*100:.0f}%)")
    elif tbr < 0.42:
        c("short", "Order Flow (approx)", 0.6, f"Verkaufsdruck (Taker-Buy {tbr*100:.0f}%)")

    # === Sentiment (Proxy): Trend des Kaufdrucks ===
    if tbr > 0.5 and tbr > float(prev["taker_buy_ratio"]):
        c("long", "Sentiment (approx)", 0.4, "Steigender Kaufdruck")
    elif tbr < 0.5 and tbr < float(prev["taker_buy_ratio"]):
        c("short", "Sentiment (approx)", 0.4, "Steigender Verkaufsdruck")

    # === Catalyst (Proxy): Volumen-/Volatilitäts-Spike ===
    if cur["vol_z"] > 2.0:
        c("long" if price > cur["open"] else "short", "Catalyst (approx)", 0.6,
          f"Volumen-Spike (z={cur['vol_z']:.1f})")

    # === Squeeze Play (Proxy): Short-/Long-Squeeze-Reversal ===
    big_bull = price > cur["open"] and (cur["high"] - cur["low"]) > 1.5 * atr_val and atr_val > 0
    big_bear = price < cur["open"] and (cur["high"] - cur["low"]) > 1.5 * atr_val and atr_val > 0
    if price < cur["ema200"] and big_bull and cur["vol_z"] > 1.5:
        c("long", "Squeeze Play (approx)", 0.6, "Mögliches Short-Squeeze-Reversal")
    if price > cur["ema200"] and big_bear and cur["vol_z"] > 1.5:
        c("short", "Squeeze Play (approx)", 0.6, "Mögliches Long-Squeeze-Reversal")

    return out


def _snapshot_row(row):
    """Extrahiert die wichtigsten Indikatorwerte einer Candle als reines dict
    (für JSON-Speicherung / spätere Analyse)."""
    keys = [
        "rsi", "macd", "macd_signal", "macd_hist",
        "ema9", "ema21", "ema50", "ema200",
        "bb_upper", "bb_mid", "bb_lower", "bb_width", "bb_squeeze",
        "stoch_k", "stoch_d", "atr", "adx", "plus_di", "minus_di",
        "close", "volume", "vol_sma20", "vol_z",
        "roc", "vwap", "obv", "taker_buy_ratio",
        "donch_high", "donch_low", "kc_upper", "kc_lower",
        "chop", "upper_wick_ratio", "lower_wick_ratio",
        "ha_bull", "tenkan", "kijun", "cloud_top", "cloud_bottom",
        "pat_bull_engulf", "pat_bear_engulf", "pat_hammer",
        "pat_shooting_star", "pat_doji", "pat_inside_bar",
    ]
    out = {}
    for k in keys:
        v = row[k]
        if isinstance(v, (np.bool_, bool)):
            out[k] = bool(v)
        elif v is None or (isinstance(v, float) and np.isnan(v)):
            out[k] = None
        else:
            out[k] = round(float(v), 8)
    return out


def analyze(symbol, klines_by_tf):
    """Hauptfunktion: bewertet einen Coin über alle Zeitrahmen.

    `klines_by_tf` ist ein dict {timeframe: kline_df}.
    Rückgabe: dict mit Signal-Infos oder None, wenn nicht genug Daten.
    """
    # Höhere Zeitrahmen bekommen mehr Gewicht (Trend-Bestätigung)
    tf_weights = {tf: 1.0 + 0.5 * i for i, tf in enumerate(config.TIMEFRAMES)}

    enriched = {}
    long_total = 0.0
    short_total = 0.0
    all_reasons = []           # (tf, side, strategy, text)
    snapshot = {}
    # Beiträge je Strategie und Richtung -> Grundlage für die Strategie-Analyse
    strat_long = {}
    strat_short = {}

    for tf, df in klines_by_tf.items():
        ind = compute_indicators(df, atr_period=config.ATR_PERIOD)
        if ind is None:
            return None
        enriched[tf] = ind
        tfw = tf_weights.get(tf, 1.0)
        for side, strategy, base_pts, text in _evaluate_timeframe(ind, tf):
            pts = base_pts * tfw * config.STRATEGY_WEIGHTS.get(strategy, 1.0)
            if pts <= 0:
                continue
            if side == "long":
                long_total += pts
                strat_long[strategy] = strat_long.get(strategy, 0.0) + pts
            else:
                short_total += pts
                strat_short[strategy] = strat_short.get(strategy, 0.0) + pts
            all_reasons.append((tf, side, strategy, text))
        snapshot[tf] = _snapshot_row(ind.iloc[-2])

    primary = enriched[config.PRIMARY_TIMEFRAME]
    last_closed = primary.iloc[-2]
    price = float(last_closed["close"])
    atr_val = float(last_closed["atr"])

    # Trend-/ADX-Filter-Infos (für die datengetriebenen Einstiegs-Gates)
    trend_up = None
    if config.TREND_FILTER_TF in enriched:
        tr = enriched[config.TREND_FILTER_TF].iloc[-2]
        trend_up = bool(tr["ema50"] > tr["ema200"])
    entry_adx = None
    if config.ADX_FILTER_TF in enriched:
        entry_adx = float(enriched[config.ADX_FILTER_TF].iloc[-2]["adx"])
    entry_bb_width = None
    if config.BB_WIDTH_FILTER_TF in enriched:
        entry_bb_width = float(enriched[config.BB_WIDTH_FILTER_TF].iloc[-2]["bb_width"])

    # Richtung bestimmen
    if long_total >= short_total:
        side = "long"
        score = long_total
        strat_points = strat_long
    else:
        side = "short"
        score = short_total
        strat_points = strat_short

    reasons_for_side = [
        f"[{tf}][{strat}] {txt}" for tf, s, strat, txt in all_reasons if s == side
    ]
    # Beitragende Strategien der gewählten Richtung, stärkste zuerst
    strategies = [
        {"name": name, "points": round(pts, 2)}
        for name, pts in sorted(strat_points.items(), key=lambda x: x[1], reverse=True)
    ]

    # Ausrichtung zum höheren Trend (Gate). trend_up=None -> kein Filter-TF da.
    trend_aligned = (
        trend_up is None
        or (side == "long" and trend_up)
        or (side == "short" and not trend_up)
    )

    return {
        "symbol": symbol,
        "side": side,
        "score": round(score, 2),
        "long_score": round(long_total, 2),
        "short_score": round(short_total, 2),
        "price": price,
        "atr": atr_val,
        "reasons": reasons_for_side,
        "strategies": strategies,
        "snapshot": snapshot,
        "primary_timeframe": config.PRIMARY_TIMEFRAME,
        "trend_up": trend_up,
        "entry_adx": round(entry_adx, 2) if entry_adx is not None else None,
        "entry_bb_width": round(entry_bb_width, 5) if entry_bb_width is not None else None,
        "trend_aligned": trend_aligned,
    }


def signal_is_valid(analysis):
    """Prüft, ob aus einer Analyse ein handelbares Signal wird."""
    if analysis is None:
        return False
    if analysis["score"] < config.ENTRY_SCORE_THRESHOLD:
        return False
    if analysis["atr"] <= 0:
        return False
    if analysis["side"] == "long" and not config.ALLOW_LONG:
        return False
    if analysis["side"] == "short" and not config.ALLOW_SHORT:
        return False
    # Datengetriebene Gates: nur mit dem 4h-Trend und nur bei genug Trendstärke.
    if config.REQUIRE_TREND_ALIGNMENT and not analysis.get("trend_aligned", True):
        return False
    if config.MIN_ENTRY_ADX:
        adx_val = analysis.get("entry_adx")
        if adx_val is not None and adx_val < config.MIN_ENTRY_ADX:
            return False
    # Marktfluktuations-Filter: toten Seitwärts-Chop (zu enge Bänder) aussperren
    if config.MIN_BB_WIDTH:
        bbw = analysis.get("entry_bb_width")
        if bbw is not None and bbw < config.MIN_BB_WIDTH:
            return False
    return True
