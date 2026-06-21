"""
Haupt-Bot (Volume-/Order-Flow): scannt nur die relevantesten Coins (Hot-List
nach Live-RVOL), eröffnet/schließt PAPER-Positionen mit simuliertem Hebel und
vollem Echtgeld-Kostenmodell (Taker-Fee, Spread, Slippage, Funding) und
speichert jeden Trade samt Volumen-Snapshot in der Datenbank.

WICHTIG: Es werden KEINE echten Orders platziert, es gibt keine API-Keys.
Alles ist eine Simulation auf echten Binance-USDT-M-Futures-Marktdaten.
"""

import logging
import time
from datetime import datetime, timezone, timedelta

import config
import database as db
import binance_client as bx
from indicators import compute_indicators
from strategy import analyze, signal_is_valid


def _setup_logging():
    import os
    os.makedirs(config.DATA_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(config.LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("bot")


log = _setup_logging()
FEE_RATE = config.TAKER_FEE_PCT / 100.0

# Funding-Raten je Symbol (Perp), periodisch aktualisiert.
_funding_rates = {}
_last_funding_refresh = 0.0


# ---------------------------------------------------------------------------
# Kosten / Fills
# ---------------------------------------------------------------------------
def _slippage_pct(rvol):
    """Slippage (%) skaliert mit dem Volumen-Surge: in heißen, schnellen Märkten
    rutscht der Fill stärker. Bounded, damit es nicht explodiert."""
    scale = min(2.5, max(0.5, (rvol or 1.0) / 2.0))
    return config.SLIPPAGE_PCT * scale


def _fill_price(price, side, is_entry, slippage_pct):
    """Realistischer Ausführungskurs inkl. halbem Spread + Slippage je Seite.
    Käufe laufen zum (höheren) Ask, Verkäufe zum (niedrigeren) Bid."""
    adverse = (config.SPREAD_PCT / 100.0) / 2.0 + (slippage_pct / 100.0)
    buying = (side == "long" and is_entry) or (side == "short" and not is_entry)
    return price * (1 + adverse) if buying else price * (1 - adverse)


def _funding_rate(symbol):
    """Funding-Rate (Bruchteil je 8 h) für ein Symbol; Default, falls unbekannt."""
    if symbol in _funding_rates:
        return _funding_rates[symbol]
    return config.FUNDING_RATE_DEFAULT_PCT_PER_8H / 100.0


# ---------------------------------------------------------------------------
# Kapital / Position-Sizing (mit Hebel)
# ---------------------------------------------------------------------------
def current_equity():
    return config.START_CAPITAL + db.realized_pnl()


def position_size(equity, price, sl_distance):
    """Stückzahl so, dass ein Stop-Treffer ~RISK_PER_TRADE_PCT des Equity kostet.
    Das Notional wird auf den Hebel-Anteil je Position gedeckelt (Margin geteilt),
    sodass die Summe aller offenen Positionen <= equity*LEVERAGE bleibt."""
    if sl_distance <= 0 or price <= 0:
        return 0.0, 0.0
    risk_amount = equity * (config.RISK_PER_TRADE_PCT / 100.0)
    qty = risk_amount / sl_distance
    notional = qty * price
    slots = max(1, config.MAX_OPEN_POSITIONS)
    max_notional = equity * config.LEVERAGE / slots
    if notional > max_notional:
        qty = max_notional / price
        notional = qty * price
    return qty, notional


def _liquidation_price(entry, side):
    """Näherung des Isolated-Margin-Liquidationskurses (ehrliche Sim bei Gaps)."""
    mmr = config.MAINTENANCE_MARGIN_PCT / 100.0
    if side == "long":
        return entry * (1 - 1 / config.LEVERAGE + mmr)
    return entry * (1 + 1 / config.LEVERAGE - mmr)


# ---------------------------------------------------------------------------
# Daten laden
# ---------------------------------------------------------------------------
def fetch_all_timeframes(symbol):
    out = {}
    for tf in config.TIMEFRAMES:
        df = bx.get_klines(symbol, tf, config.KLINE_LIMIT)
        if df.empty:
            return None
        out[tf] = df
        time.sleep(config.REQUEST_SLEEP_SECONDS)
    return out


def rank_hot_symbols(universe):
    """Wählt aus dem Basis-Universe die Coins mit dem stärksten LIVE-Volumen-Surge
    (RVOL) -> die gerade 'relevantesten' Coins. Leichtgewichtig (1m, kurze Klines)."""
    scored = []
    for sym in universe:
        try:
            df = bx.get_klines(sym, config.PRIMARY_TIMEFRAME, 60)
            time.sleep(config.REQUEST_SLEEP_SECONDS)
            if df.empty or len(df) < 25:
                continue
            vol = df["volume"]
            avg = vol.iloc[-21:-1].mean()        # Schnitt der letzten 20 (ohne laufende)
            last = float(vol.iloc[-2])           # letzte abgeschlossene Candle
            rvol = last / avg if avg > 0 else 0.0
            scored.append((sym, rvol))
        except Exception as e:
            log.debug("RVOL-Rank Fehler %s: %s", sym, e)
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in scored[:config.HOT_LIST_N]]


# ---------------------------------------------------------------------------
# Position eröffnen
# ---------------------------------------------------------------------------
def open_position(a):
    equity = current_equity()
    side = a["side"]
    slip = _slippage_pct(a.get("rvol"))
    price = _fill_price(a["price"], side, is_entry=True, slippage_pct=slip)
    atr = a["atr"]

    # Schutz-Stop (katastrophal): ATR-basiert, gedeckelt klar innerhalb der Liquidation
    sl_distance = min(config.STOP_ATR_MULT * atr, price * config.MAX_STOP_PCT / 100.0)
    if sl_distance <= 0:
        return None

    # Ziel: struktur-basiert (POC/nächstes Volume-Node), sonst ATR-Fallback
    target = a.get("target")
    if side == "long":
        stop_loss = price - sl_distance
        take_profit = target if (target and target > price) else price + config.TP_ATR_MULT * atr
    else:
        stop_loss = price + sl_distance
        take_profit = target if (target and target < price) else price - config.TP_ATR_MULT * atr

    qty, notional = position_size(equity, price, sl_distance)
    if qty <= 0:
        return None

    entry_fee = notional * FEE_RATE
    trade_id = db.open_trade(
        symbol=a["symbol"], side=side, entry_price=price, qty=qty,
        stop_loss=stop_loss, take_profit=take_profit,
        entry_score=a["score"], long_score=a["long_score"], short_score=a["short_score"],
        primary_tf=a["primary_timeframe"], entry_reasons=a["reasons"],
        indicators=a["snapshot"], fee=entry_fee, strategies=a.get("strategies", []),
        leverage=config.LEVERAGE,
    )
    log.info(
        "OPEN  #%s %-12s %-5s @ %.6g | score=%.1f rvol=%.1f flow=%.2f SL=%.6g TP=%.6g notional=%.2f",
        trade_id, a["symbol"], side.upper(), price, a["score"], a.get("rvol", 0),
        a.get("flow_imbalance", 0), stop_loss, take_profit, notional,
    )
    return trade_id


# ---------------------------------------------------------------------------
# Position schließen
# ---------------------------------------------------------------------------
def _parse_time(s):
    return datetime.fromisoformat(s)


def close_position(trade, exit_price, exit_reason, slippage_pct):
    qty = trade["qty"]
    entry = trade["entry_price"]
    side = trade["side"]

    exit_price = _fill_price(exit_price, side, is_entry=False, slippage_pct=slippage_pct)
    if side == "long":
        gross = qty * (exit_price - entry)
    else:
        gross = qty * (entry - exit_price)

    exit_fee = qty * exit_price * FEE_RATE
    total_fees = (trade["fees"] or 0.0) + exit_fee

    hold_minutes = (
        datetime.now(timezone.utc) - _parse_time(trade["entry_time"])
    ).total_seconds() / 60.0

    # Funding (signiert): Long zahlt bei positiver Rate, Short erhält sie.
    rate = _funding_rate(trade["symbol"])
    side_sign = 1.0 if side == "long" else -1.0
    funding = side_sign * (trade["notional"] or 0.0) * rate * (hold_minutes / 60.0 / 8.0)

    pnl = gross - total_fees - funding
    pnl_pct = (pnl / trade["notional"]) * 100 if trade["notional"] else 0.0

    db.close_trade(trade["id"], exit_price, exit_reason, pnl, pnl_pct,
                   total_fees, hold_minutes, funding=funding)
    log.info(
        "CLOSE #%s %-12s %-5s @ %.6g | %-11s PnL=%.2f (%.2f%%) fund=%.3f hold=%.0fm",
        trade["id"], trade["symbol"], side.upper(), exit_price,
        exit_reason, pnl, pnl_pct, funding, hold_minutes,
    )


def _flow_against(ind, side):
    """True, wenn der aggressive Order-Flow auf 1m klar GEGEN die Position dreht."""
    cur = ind.iloc[-2]
    vol_sma = float(cur["vol_sma"]) if cur["vol_sma"] == cur["vol_sma"] and cur["vol_sma"] else 0.0
    delta_sma = float(cur["delta_sma"]) if cur["delta_sma"] == cur["delta_sma"] else 0.0
    fi = (delta_sma / vol_sma / 2.0) if vol_sma > 0 else 0.0
    cvd_roc = float(cur["cvd_roc"]) if cur["cvd_roc"] == cur["cvd_roc"] else 0.0
    if side == "long":
        return fi < -config.MIN_TAKER_IMBALANCE and cvd_roc < 0
    return fi > config.MIN_TAKER_IMBALANCE and cvd_roc > 0


def manage_open_positions():
    """Prüft je offene Position: Liquidation/Stop/Ziel/Order-Flow-Flip/Timeout.

    Ein einzelner Fehler (z.B. ein altbestehender Trade auf einem Symbol, das
    auf Futures nicht (mehr) gelistet ist) darf den ganzen Zyklus NICHT killen.
    """
    for trade in db.get_open_trades():
        try:
            _manage_one(trade)
        except Exception as e:
            log.warning("Fehler beim Verwalten von %s (#%s): %s",
                        trade.get("symbol"), trade.get("id"), e)


def _manage_one(trade):
    symbol = trade["symbol"]
    df = bx.get_klines(symbol, config.PRIMARY_TIMEFRAME, 60)
    time.sleep(config.REQUEST_SLEEP_SECONDS)
    if df.empty:
        return

    live_price = float(df["close"].iloc[-1])
    recent_high = float(df["high"].iloc[-2:].max())
    recent_low = float(df["low"].iloc[-2:].min())

    side = trade["side"]
    sl, tp = trade["stop_loss"], trade["take_profit"]
    liq = _liquidation_price(trade["entry_price"], side)

    ind = compute_indicators(df, atr_period=config.ATR_PERIOD)
    rvol = float(ind.iloc[-2]["rvol"]) if ind is not None else 1.0
    slip = _slippage_pct(rvol)

    # 1) Liquidation (hart) -> 2) Stop -> 3) Ziel
    if side == "long":
        if recent_low <= liq:
            return close_position(trade, liq, "liquidation", slip)
        if recent_low <= sl:
            return close_position(trade, sl, "stop_loss", slip)
        if recent_high >= tp:
            return close_position(trade, tp, "take_profit", slip)
    else:
        if recent_high >= liq:
            return close_position(trade, liq, "liquidation", slip)
        if recent_high >= sl:
            return close_position(trade, sl, "stop_loss", slip)
        if recent_low <= tp:
            return close_position(trade, tp, "take_profit", slip)

    hold_min = (datetime.now(timezone.utc) - _parse_time(trade["entry_time"])).total_seconds() / 60.0

    # 4) Order-Flow-Flip (Primär-Exit): der Volumengrund ist weg
    if (config.ORDER_FLOW_FLIP_EXIT and ind is not None
            and hold_min >= config.MIN_HOLD_MINUTES and _flow_against(ind, side)):
        return close_position(trade, live_price, "flow_flip", slip)

    # 5) Zeit-Stop ("Halten nicht über Stunden")
    if hold_min >= config.MAX_HOLD_MINUTES:
        return close_position(trade, live_price, "timeout", slip)


# ---------------------------------------------------------------------------
# Haupt-Loop
# ---------------------------------------------------------------------------
def _refresh_funding():
    global _funding_rates, _last_funding_refresh
    if time.time() - _last_funding_refresh > 3600:
        try:
            fr = bx.get_funding_rates()
            if fr:
                _funding_rates = fr
            _last_funding_refresh = time.time()
        except Exception as e:
            log.debug("Funding-Refresh Fehler: %s", e)


def run():
    db.init_db()
    if not bx.ping():
        log.error("Binance-API nicht erreichbar. Beende.")
        return

    log.info("=" * 70)
    log.info("Volume-/Order-Flow-Bot (PAPER) gestartet — Markt: %s, Hebel: %.0fx",
             "FUTURES" if config.USE_FUTURES else "SPOT", config.LEVERAGE)
    log.info("Kapital: %.2f %s | Universe top %d -> Hot-List %d | TF: %s | Scan %ds",
             config.START_CAPITAL, config.QUOTE_ASSET, config.BASE_UNIVERSE_N,
             config.HOT_LIST_N, ", ".join(config.TIMEFRAMES), config.SCAN_INTERVAL_SECONDS)
    log.info("Gates: score>=%.1f, RVOL>=%.1f, |flow|>=%.2f | Kosten: fee %.3f%% spread %.3f%% slip %.3f%%",
             config.ENTRY_SCORE_THRESHOLD, config.MIN_RVOL, config.MIN_TAKER_IMBALANCE,
             config.TAKER_FEE_PCT, config.SPREAD_PCT, config.SLIPPAGE_PCT)
    log.info("=" * 70)

    universe = []
    hot = []
    last_universe_refresh = 0.0
    last_hotlist_refresh = 0.0

    while True:
        cycle_start = time.time()
        try:
            _refresh_funding()

            # Basis-Universe (liquideste Perps) periodisch laden
            if time.time() - last_universe_refresh > config.SYMBOL_REFRESH_MINUTES * 60:
                universe = bx.get_top_symbols()
                last_universe_refresh = time.time()
                log.info("Basis-Universe aktualisiert: %d Coins", len(universe))

            # Hot-List (Live-RVOL-Ranking) periodisch neu bestimmen
            if universe and time.time() - last_hotlist_refresh > config.HOTLIST_REFRESH_SECONDS:
                hot = rank_hot_symbols(universe)
                last_hotlist_refresh = time.time()
                log.info("Hot-List: %s", ", ".join(hot) if hot else "—")

            # 1) Offene Positionen verwalten
            manage_open_positions()

            # 2) Hot-List nach Einstiegen scannen
            open_syms = db.get_open_symbols()
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(minutes=config.REENTRY_COOLDOWN_MINUTES)).isoformat()
            cooling = db.symbols_closed_since(cutoff)
            opened = 0
            for symbol in hot:
                if config.MAX_OPEN_POSITIONS and db.count_open() >= config.MAX_OPEN_POSITIONS:
                    break
                if symbol in open_syms or symbol in cooling:
                    continue
                try:
                    tfs = fetch_all_timeframes(symbol)
                    if tfs is None:
                        continue
                    a = analyze(symbol, tfs)
                    if signal_is_valid(a):
                        if open_position(a):
                            open_syms.add(symbol)
                            opened += 1
                except Exception as e:
                    log.warning("Fehler bei %s: %s", symbol, e)

            # 3) Equity protokollieren
            eq = current_equity()
            db.record_equity(eq, db.count_open())
            log.info("Scan fertig in %.0fs | Equity=%.2f | offen=%d | neu=%d",
                     time.time() - cycle_start, eq, db.count_open(), opened)

        except KeyboardInterrupt:
            log.info("Manuell beendet.")
            break
        except Exception as e:
            log.exception("Unerwarteter Fehler im Loop: %s", e)

        elapsed = time.time() - cycle_start
        time.sleep(max(5, config.SCAN_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    run()
