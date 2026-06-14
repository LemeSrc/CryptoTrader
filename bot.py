"""
Haupt-Bot: Scannt alle Coins, eröffnet und schließt PAPER-Positionen und
speichert jeden Trade samt Indikator-Snapshot in der Datenbank.

WICHTIG: Es werden KEINE echten Orders platziert. Es gibt keine API-Keys.
Alles ist eine Simulation auf Basis echter Binance-Marktdaten.
"""

import logging
import time
from datetime import datetime, timezone, timedelta

import config
import database as db
import binance_client as bx
from strategy import analyze, signal_is_valid


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
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


def _fill_price(price, side, is_entry):
    """Realistischer Ausführungskurs inkl. halbem Spread je Seite.

    Käufe laufen zum (höheren) Ask, Verkäufe zum (niedrigeren) Bid -> jeder
    Round-Trip kostet zusätzlich zum Taker-Fee den vollen SPREAD_PCT.
    """
    half_spread = (config.SPREAD_PCT / 100.0) / 2.0
    buying = (side == "long" and is_entry) or (side == "short" and not is_entry)
    return price * (1 + half_spread) if buying else price * (1 - half_spread)


# ---------------------------------------------------------------------------
# Kapital / Position-Sizing
# ---------------------------------------------------------------------------
def current_equity():
    """Virtuelles Kapital = Startkapital + realisierte Gewinne/Verluste."""
    return config.START_CAPITAL + db.realized_pnl()


def position_size(equity, price, sl_distance):
    """Berechnet Stückzahl, sodass ein SL-Treffer ~RISK_PER_TRADE_PCT kostet.

    Begrenzt das Volumen zusätzlich auf max. das Equity (kein Hebel).
    """
    if sl_distance <= 0:
        return 0.0, 0.0
    risk_amount = equity * (config.RISK_PER_TRADE_PCT / 100.0)
    qty = risk_amount / sl_distance
    notional = qty * price
    max_notional = equity  # kein Hebel im Paper-Modus
    if notional > max_notional:
        qty = max_notional / price
        notional = qty * price
    return qty, notional


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


# ---------------------------------------------------------------------------
# Position eröffnen
# ---------------------------------------------------------------------------
def open_position(analysis):
    equity = current_equity()
    side = analysis["side"]
    # Einstieg zum realistischen Fill (inkl. Spread), nicht zum Mittelkurs
    price = _fill_price(analysis["price"], side, is_entry=True)
    atr = analysis["atr"]

    sl_distance = config.SL_ATR_MULTIPLIER * atr
    tp_distance = config.TP_ATR_MULTIPLIER * atr
    if side == "long":
        stop_loss = price - sl_distance
        take_profit = price + tp_distance
    else:
        stop_loss = price + sl_distance
        take_profit = price - tp_distance

    qty, notional = position_size(equity, price, sl_distance)
    if qty <= 0:
        return None

    entry_fee = notional * FEE_RATE
    trade_id = db.open_trade(
        symbol=analysis["symbol"],
        side=side,
        entry_price=price,
        qty=qty,
        stop_loss=stop_loss,
        take_profit=take_profit,
        entry_score=analysis["score"],
        long_score=analysis["long_score"],
        short_score=analysis["short_score"],
        primary_tf=analysis["primary_timeframe"],
        entry_reasons=analysis["reasons"],
        indicators=analysis["snapshot"],
        fee=entry_fee,
        strategies=analysis.get("strategies", []),
    )
    log.info(
        "OPEN  #%s %-12s %-5s @ %.6g | score=%.1f SL=%.6g TP=%.6g notional=%.2f",
        trade_id, analysis["symbol"], side.upper(), price,
        analysis["score"], stop_loss, take_profit, notional,
    )
    return trade_id


# ---------------------------------------------------------------------------
# Position schließen
# ---------------------------------------------------------------------------
def _parse_time(s):
    return datetime.fromisoformat(s)


def close_position(trade, exit_price, exit_reason):
    qty = trade["qty"]
    entry = trade["entry_price"]
    side = trade["side"]

    # Ausstieg ebenfalls zum realistischen Fill (inkl. Spread)
    exit_price = _fill_price(exit_price, side, is_entry=False)

    if side == "long":
        gross = qty * (exit_price - entry)
    else:
        gross = qty * (entry - exit_price)

    exit_notional = qty * exit_price
    exit_fee = exit_notional * FEE_RATE
    total_fees = (trade["fees"] or 0.0) + exit_fee
    pnl = gross - total_fees
    pnl_pct = (pnl / trade["notional"]) * 100 if trade["notional"] else 0.0

    hold_minutes = (
        datetime.now(timezone.utc) - _parse_time(trade["entry_time"])
    ).total_seconds() / 60.0

    db.close_trade(trade["id"], exit_price, exit_reason, pnl, pnl_pct,
                   total_fees, hold_minutes)
    log.info(
        "CLOSE #%s %-12s %-5s @ %.6g | %-11s PnL=%.2f (%.2f%%) hold=%.0fm",
        trade["id"], trade["symbol"], side.upper(), exit_price,
        exit_reason, pnl, pnl_pct, hold_minutes,
    )


def manage_open_positions():
    """Prüft für jede offene Position, ob SL/TP/Timeout/Gegensignal greift."""
    open_trades = db.get_open_trades()
    for trade in open_trades:
        symbol = trade["symbol"]
        df = bx.get_klines(symbol, config.PRIMARY_TIMEFRAME, 5)
        time.sleep(config.REQUEST_SLEEP_SECONDS)
        if df.empty:
            continue

        # Live-Preis = Close der noch laufenden Candle
        live_price = float(df["close"].iloc[-1])
        # Hoch/Tief seit dem letzten Scan (letzte ~2 Candles)
        recent_high = float(df["high"].iloc[-2:].max())
        recent_low = float(df["low"].iloc[-2:].min())

        side = trade["side"]
        sl = trade["stop_loss"]
        tp = trade["take_profit"]

        # 1) Stop-Loss / Take-Profit (SL konservativ zuerst geprüft)
        if side == "long":
            if recent_low <= sl:
                close_position(trade, sl, "stop_loss")
                continue
            if recent_high >= tp:
                close_position(trade, tp, "take_profit")
                continue
        else:  # short
            if recent_high >= sl:
                close_position(trade, sl, "stop_loss")
                continue
            if recent_low <= tp:
                close_position(trade, tp, "take_profit")
                continue

        # 2) Timeout
        hold_min = (datetime.now(timezone.utc) - _parse_time(trade["entry_time"])).total_seconds() / 60.0
        if hold_min / 60.0 >= config.MAX_HOLD_HOURS:
            close_position(trade, live_price, "timeout")
            continue

        # 3) Klares Gegensignal -> aber erst nach Mindesthaltedauer (Anti-Churn).
        #    Verhindert das Flip-Flop im 1-Candle-Takt; SL/TP greifen unabhängig.
        if config.EXIT_ON_OPPOSITE_SIGNAL and hold_min >= config.MIN_HOLD_MINUTES:
            tfs = fetch_all_timeframes(symbol)
            if tfs is None:
                continue
            a = analyze(symbol, tfs)
            if a and a["side"] != side and a["score"] >= config.ENTRY_SCORE_THRESHOLD * 1.5:
                close_position(trade, live_price, "opposite")
                continue


# ---------------------------------------------------------------------------
# Haupt-Loop
# ---------------------------------------------------------------------------
def run():
    db.init_db()
    if not bx.ping():
        log.error("Binance-API nicht erreichbar. Beende.")
        return

    log.info("=" * 70)
    log.info("Crypto Trading Bot (PAPER-MODUS) gestartet")
    log.info("Startkapital: %.2f %s | Coins: top %d | Zeitrahmen: %s",
             config.START_CAPITAL, config.QUOTE_ASSET, config.TOP_N_SYMBOLS,
             ", ".join(config.TIMEFRAMES))
    log.info("Einstiegs-Score-Schwelle: %.1f (niedrig = viele Trades)",
             config.ENTRY_SCORE_THRESHOLD)
    log.info("=" * 70)

    symbols = []
    last_symbol_refresh = 0.0

    while True:
        cycle_start = time.time()
        try:
            # Coin-Liste periodisch aktualisieren
            if time.time() - last_symbol_refresh > config.SYMBOL_REFRESH_MINUTES * 60:
                symbols = bx.get_top_symbols()
                last_symbol_refresh = time.time()
                log.info("Coin-Liste aktualisiert: %d Coins", len(symbols))

            # 1) Offene Positionen verwalten
            manage_open_positions()

            # 2) Nach neuen Signalen suchen
            open_syms = db.get_open_symbols()
            # Coins im Wiedereinstiegs-Cooldown (kürzlich geschlossen) überspringen
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(minutes=config.REENTRY_COOLDOWN_MINUTES)).isoformat()
            cooling = db.symbols_closed_since(cutoff)
            opened = 0
            for symbol in symbols:
                # MAX_OPEN_POSITIONS = None -> keine Obergrenze
                if config.MAX_OPEN_POSITIONS and db.count_open() >= config.MAX_OPEN_POSITIONS:
                    break
                if symbol in open_syms:
                    continue  # schon eine Position auf diesem Coin
                if symbol in cooling:
                    continue  # erst nach Cooldown wieder handelbar
                try:
                    tfs = fetch_all_timeframes(symbol)
                    if tfs is None:
                        continue
                    a = analyze(symbol, tfs)
                    if signal_is_valid(a):
                        if open_position(a):
                            open_syms.add(symbol)
                            opened += 1
                except Exception as e:  # einzelner Coin darf den Loop nicht killen
                    log.warning("Fehler bei %s: %s", symbol, e)

            # 3) Equity protokollieren
            eq = current_equity()
            db.record_equity(eq, db.count_open())
            log.info(
                "Scan fertig in %.0fs | Equity=%.2f | offen=%d | neu eröffnet=%d",
                time.time() - cycle_start, eq, db.count_open(), opened,
            )

        except KeyboardInterrupt:
            log.info("Manuell beendet.")
            break
        except Exception as e:
            log.exception("Unerwarteter Fehler im Loop: %s", e)

        # Bis zum nächsten Scan warten
        elapsed = time.time() - cycle_start
        sleep_for = max(5, config.SCAN_INTERVAL_SECONDS - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    run()
