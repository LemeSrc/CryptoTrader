"""
Signal-Logik — VOLUMEN-Confluence.

Aus den reinen Volumen-Indikatoren (Volume Profile, Order Flow/CVD, RVOL,
VWAP, MFI, OBV) wird ein Long-/Short-Score gebildet. Entscheidend sind aber
nicht fein getunte Gewichte (die overfitten je Marktregime), sondern robuste
GATES: nur bei echtem Volumen-Surge, nur an sinnvollen Volumen-Niveaus, nie
gegen den aggressiven Order-Flow. Das hält die Trade-Zahl niedrig.

Interface bleibt kompatibel: analyze(symbol, klines_by_tf) / signal_is_valid(a).
"""

import numpy as np

import config
from indicators import (
    compute_indicators, volume_profile, next_node, delta_divergence,
)

# Signale, die eine echte Volumen-"Location" markieren (Location-Gate).
_LOCATION_STRATS = {"Value Reversion", "Volume Breakout", "POC Acceptance", "VWAP Reaction"}


def _near(price, level, atr_val, k):
    if level is None or atr_val is None or atr_val <= 0:
        return False
    return abs(price - level) <= k * atr_val


def _imbalance(row):
    """Signierter Order-Flow-Anteil dieser Candle: taker_buy_ratio - 0,5."""
    return float(row["taker_buy_ratio"]) - 0.5


def _flow_signals(ind, tf):
    """Order-Flow-/Volumen-Signale eines Zeitrahmens (auf der geschlossenen Candle)."""
    cur, prev = ind.iloc[-2], ind.iloc[-3]
    out = []

    def c(side, strat, pts, text):
        out.append((side, strat, pts, text))

    rvol = float(cur["rvol"])
    imb = _imbalance(cur)
    cvd_roc = float(cur["cvd_roc"]) if cur["cvd_roc"] == cur["cvd_roc"] else 0.0

    # --- Order Flow: aggressives Taker-Ungleichgewicht (+ jüngster CVD-Druck) ---
    if imb >= config.MIN_TAKER_IMBALANCE:
        c("long", "Order Flow", 1.0, f"Kaufdruck (Taker {(_imbalance(cur)+0.5)*100:.0f}%)")
    elif imb <= -config.MIN_TAKER_IMBALANCE:
        c("short", "Order Flow", 1.0, f"Verkaufsdruck (Taker {(_imbalance(cur)+0.5)*100:.0f}%)")
    if cvd_roc > 0:
        c("long", "Order Flow", 0.5, "CVD steigt (Netto-Kaufdelta)")
    elif cvd_roc < 0:
        c("short", "Order Flow", 0.5, "CVD fällt (Netto-Verkaufsdelta)")

    # --- VWAP-Reaktion: Reclaim / Verlust des Volumen-Mittels mit Delta ---
    if cur["vwap"] == cur["vwap"]:
        if prev["close"] <= prev["vwap"] and cur["close"] > cur["vwap"] and imb > 0:
            c("long", "VWAP Reaction", 0.8, "VWAP-Reclaim mit Kaufdruck")
        if prev["close"] >= prev["vwap"] and cur["close"] < cur["vwap"] and imb < 0:
            c("short", "VWAP Reaction", 0.8, "VWAP-Verlust mit Verkaufsdruck")

    # --- MFI-Extrem (volumengewichtet) ---
    if cur["mfi"] < 20:
        c("long", "MFI", 0.5, f"MFI überverkauft ({cur['mfi']:.0f})")
    elif cur["mfi"] > 80:
        c("short", "MFI", 0.5, f"MFI überkauft ({cur['mfi']:.0f})")

    # --- Order-Flow-Divergenz (Preis vs. CVD) ---
    div = delta_divergence(ind)
    if div == "bull":
        c("long", "Delta Divergence", 1.0, "Bullische CVD-Divergenz")
    elif div == "bear":
        c("short", "Delta Divergence", 1.0, "Bärische CVD-Divergenz")

    # --- OBV-Bestätigung ---
    obv_slope = float(cur["obv_slope"]) if cur["obv_slope"] == cur["obv_slope"] else 0.0
    if obv_slope > 0:
        c("long", "OBV", 0.4, "OBV steigt")
    elif obv_slope < 0:
        c("short", "OBV", 0.4, "OBV fällt")

    return out


def _profile_signals(price, prev_price, atr_val, prim, profile):
    """Volume-Profile-Signale (Value-Edge-Reversion, Breakout, POC) aus dem
    Profil (PROFILE_TF) gegen den aktuellen Preis."""
    out = []
    if profile is None:
        return out

    def c(side, strat, pts, text):
        out.append((side, strat, pts, text))

    poc, vah, val = profile["poc"], profile["vah"], profile["val"]
    rvol = float(prim["rvol"])
    cvd_roc = float(prim["cvd_roc"]) if prim["cvd_roc"] == prim["cvd_roc"] else 0.0
    near = config.NEAR_NODE_ATR

    # --- Value-Edge-Reversion: an VAL kaufen / an VAH verkaufen, wenn der
    #     Order-Flow nicht mehr gegen die Richtung drückt (Absorption). ---
    if (price <= val or _near(price, val, atr_val, near)) and cvd_roc >= 0:
        c("long", "Value Reversion", 1.2, "Reversion an Value-Area-Low (VAL)")
    if (price >= vah or _near(price, vah, atr_val, near)) and cvd_roc <= 0:
        c("short", "Value Reversion", 1.2, "Reversion an Value-Area-High (VAH)")

    # --- Breakout/Acceptance: Ausbruch aus der Value Area mit Surge + Flow ---
    if (prev_price <= vah < price) and rvol >= config.MIN_RVOL and cvd_roc > 0:
        c("long", "Volume Breakout", 1.2, "Ausbruch über VAH mit Volumen+CVD")
    if (prev_price >= val > price) and rvol >= config.MIN_RVOL and cvd_roc < 0:
        c("short", "Volume Breakout", 1.2, "Ausbruch unter VAL mit Volumen+CVD")

    # --- POC-Acceptance: Annahme über/unter dem Point of Control ---
    if (prev_price <= poc < price) and cvd_roc > 0:
        c("long", "POC Acceptance", 0.8, "Annahme über POC")
    if (prev_price >= poc > price) and cvd_roc < 0:
        c("short", "POC Acceptance", 0.8, "Annahme unter POC")

    return out


def _snapshot_row(row, extra=None):
    keys = [
        "close", "volume", "quote_volume", "atr",
        "rvol", "vol_sma", "taker_buy_ratio", "delta", "delta_sma",
        "cvd", "cvd_roc", "vwap", "vwap_dist_atr", "mfi", "obv_slope",
    ]
    out = {}
    for k in keys:
        v = row[k] if k in row else None
        if isinstance(v, (np.bool_, bool)):
            out[k] = bool(v)
        elif v is None or (isinstance(v, float) and np.isnan(v)):
            out[k] = None
        else:
            out[k] = round(float(v), 8)
    if extra:
        out.update(extra)
    return out


def analyze(symbol, klines_by_tf):
    """Bewertet einen Coin über 1m (Order-Flow) + 5m (Kontext/Profil).

    `klines_by_tf` = {timeframe: kline_df}. Rückgabe: Signal-dict oder None.
    """
    enriched = {}
    for tf, df in klines_by_tf.items():
        ind = compute_indicators(df, atr_period=config.ATR_PERIOD)
        if ind is None:
            return None
        enriched[tf] = ind

    if config.PRIMARY_TIMEFRAME not in enriched:
        return None
    prim = enriched[config.PRIMARY_TIMEFRAME]
    last = prim.iloc[-2]
    prev = prim.iloc[-3]
    price = float(last["close"])
    prev_price = float(prev["close"])
    atr_val = float(last["atr"]) if last["atr"] == last["atr"] else 0.0

    # Volume Profile auf dem Profil-Zeitrahmen (5m)
    prof_df = enriched.get(config.PROFILE_TF, prim)
    profile = volume_profile(prof_df, lookback=config.VP_LOOKBACK,
                             bins=config.VP_BINS,
                             value_area_pct=config.VALUE_AREA_PCT)

    long_total = 0.0
    short_total = 0.0
    strat_long, strat_short = {}, {}
    all_reasons = []   # (tf, side, strat, text)

    # Flow-Signale je Zeitrahmen (1m voll, 5m als Kontext-Bestätigung leichter)
    tf_weight = {config.PRIMARY_TIMEFRAME: 1.0, config.CONTEXT_TF: 0.8}
    for tf, ind in enriched.items():
        w = tf_weight.get(tf, 0.8)
        for side, strat, pts, text in _flow_signals(ind, tf):
            p = pts * w
            if side == "long":
                long_total += p
                strat_long[strat] = strat_long.get(strat, 0.0) + p
            else:
                short_total += p
                strat_short[strat] = strat_short.get(strat, 0.0) + p
            all_reasons.append((tf, side, strat, text))

    # Profil-Signale (einmal, gegen aktuellen Preis)
    for side, strat, pts, text in _profile_signals(price, prev_price, atr_val, last, profile):
        if side == "long":
            long_total += pts
            strat_long[strat] = strat_long.get(strat, 0.0) + pts
        else:
            short_total += pts
            strat_short[strat] = strat_short.get(strat, 0.0) + pts
        all_reasons.append((config.PROFILE_TF, side, strat, text))

    # Richtung
    if long_total >= short_total:
        side, score, strat_points = "long", long_total, strat_long
    else:
        side, score, strat_points = "short", short_total, strat_short

    reasons_for_side = [f"[{tf}][{s}] {t}" for tf, sd, s, t in all_reasons if sd == side]
    strat_names = {s for _tf, sd, s, _t in all_reasons if sd == side}
    strategies = [
        {"name": n, "points": round(p, 2)}
        for n, p in sorted(strat_points.items(), key=lambda x: x[1], reverse=True)
    ]
    has_location = bool(strat_names & _LOCATION_STRATS)

    # Order-Flow-Lage (für das Agreement-Gate): geglättetes Delta / Volumen
    vol_sma = float(last["vol_sma"]) if last["vol_sma"] == last["vol_sma"] and last["vol_sma"] else 0.0
    delta_sma = float(last["delta_sma"]) if last["delta_sma"] == last["delta_sma"] else 0.0
    flow_imbalance = (delta_sma / vol_sma / 2.0) if vol_sma > 0 else 0.0

    # Struktur-Ziel: zum POC zurück (Magnet) bzw. nächstes Node in Richtung
    target = None
    if profile is not None:
        if side == "long":
            target = profile["poc"] if price < profile["poc"] else next_node(profile, price, +1)
        else:
            target = profile["poc"] if price > profile["poc"] else next_node(profile, price, -1)

    snapshot = {
        config.PRIMARY_TIMEFRAME: _snapshot_row(
            last, extra={"poc": profile["poc"] if profile else None,
                         "vah": profile["vah"] if profile else None,
                         "val": profile["val"] if profile else None}),
        config.CONTEXT_TF: _snapshot_row(enriched[config.CONTEXT_TF].iloc[-2])
        if config.CONTEXT_TF in enriched else {},
    }

    return {
        "symbol": symbol,
        "side": side,
        "score": round(score, 2),
        "long_score": round(long_total, 2),
        "short_score": round(short_total, 2),
        "price": price,
        "atr": atr_val,
        "rvol": round(float(last["rvol"]), 2),
        "flow_imbalance": round(flow_imbalance, 4),
        "taker_buy_ratio": round(float(last["taker_buy_ratio"]), 4),
        "cvd_roc": round(float(last["cvd_roc"]), 2) if last["cvd_roc"] == last["cvd_roc"] else 0.0,
        "has_location": has_location,
        "profile": profile and {"poc": profile["poc"], "vah": profile["vah"], "val": profile["val"]},
        "target": target,
        "reasons": reasons_for_side,
        "strategies": strategies,
        "snapshot": snapshot,
        "primary_timeframe": config.PRIMARY_TIMEFRAME,
    }


def signal_is_valid(analysis):
    """Volumen-Gates: nur wenige, hochwertige Setups passieren."""
    if analysis is None:
        return False
    a = analysis
    if a["score"] < config.ENTRY_SCORE_THRESHOLD:
        return False
    if a["atr"] <= 0:
        return False
    if a["side"] == "long" and not config.ALLOW_LONG:
        return False
    if a["side"] == "short" and not config.ALLOW_SHORT:
        return False
    # RVOL-Gate: nur bei echtem Volumen-Surge handeln (kein toter Chop).
    if a["rvol"] < config.MIN_RVOL:
        return False
    # Location-Gate: nur an einer echten Volumen-Node (Value-Edge/Breakout/POC/VWAP).
    if not a["has_location"]:
        return False
    # Order-Flow-Agreement: nicht klar gegen den aggressiven Flow handeln.
    fi = a["flow_imbalance"]
    if a["side"] == "long" and fi < -config.MIN_TAKER_IMBALANCE:
        return False
    if a["side"] == "short" and fi > config.MIN_TAKER_IMBALANCE:
        return False
    # Kosten-Gate (Lehre Nr. 1: Kosten töten): der Weg zum Ziel muss die
    # geschätzten Round-Trip-Kosten deutlich übersteigen, sonst kein Trade.
    price = a["price"]
    target = a.get("target")
    edge = abs(target - price) if target else config.TP_ATR_MULT * a["atr"]
    cost_price = price * (2 * config.TAKER_FEE_PCT + config.SPREAD_PCT
                          + 2 * config.SLIPPAGE_PCT) / 100.0
    if edge < config.MIN_EDGE_COST_RATIO * cost_price:
        return False
    return True
