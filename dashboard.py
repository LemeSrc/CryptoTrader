"""
Web-Dashboard zur Analyse der (Paper-)Trades.

Start:  python dashboard.py
Dann im Browser:  http://127.0.0.1:5000

Liefert Kennzahlen, Equity-Kurve, offene Positionen, alle Trades (filterbar)
sowie Auswertungen nach Coin, Richtung, Exit-Grund und Einstiegs-RSI.
Außerdem CSV-Export aller Trades für die spätere Feinjustierung.
"""

import csv
import hmac
import io
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, render_template, request, Response

import config
import database as db
import binance_client as bx

app = Flask(__name__)


@app.before_request
def _require_auth():
    """Optionaler HTTP-Basic-Auth-Schutz. Aktiv, sobald CT_DASHBOARD_PASSWORD
    gesetzt ist -> schützt das öffentlich gehostete Dashboard (inkl. Reset)."""
    pw = config.DASHBOARD_PASSWORD
    if not pw:
        return  # kein Passwort konfiguriert -> offen (lokaler Betrieb)
    auth = request.authorization
    if (auth and hmac.compare_digest(auth.username or "", config.DASHBOARD_USER)
            and hmac.compare_digest(auth.password or "", pw)):
        return
    return Response("Authentifizierung erforderlich", 401,
                    {"WWW-Authenticate": 'Basic realm="CryptoTrader"'})

# Zeitfenster-Presets (Sekunden) und Handels-Sessions (UTC-Stunden, mit
# realistischer Überlappung) für die kombinierten Dashboard-Filter.
RANGE_SECONDS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
SESSIONS = {
    "asia": range(0, 8),       # ~Tokio/Sydney
    "europe": range(7, 16),    # ~London/Frankfurt
    "us": range(13, 22),       # ~New York
}


def _parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _apply_filters(trades):
    """Filtert Trades nach den Query-Parametern `range` (24h/7d/30d) und
    `session` (asia/europe/us). Ohne/unbekannte Werte = keine Einschränkung."""
    rng = request.args.get("range")
    session = request.args.get("session")
    out = trades
    if rng in RANGE_SECONDS:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=RANGE_SECONDS[rng])
        out = [t for t in out
               if (dt := _parse_iso(t.get("entry_time"))) is not None and dt >= cutoff]
    if session in SESSIONS:
        hours = SESSIONS[session]
        out = [t for t in out
               if (dt := _parse_iso(t.get("entry_time"))) is not None
               and dt.astimezone(timezone.utc).hour in hours]
    return out


# ---------------------------------------------------------------------------
# Helfer
# ---------------------------------------------------------------------------
def _closed(trades):
    return [t for t in trades if t["status"] == "closed" and t["pnl"] is not None]


def _winrate(trades):
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t["pnl"] > 0)
    return 100.0 * wins / len(trades)


def _summary(closed):
    pnls = [t["pnl"] for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "count": len(closed),
        "winrate": round(_winrate(closed), 2),
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(statistics.mean(pnls), 4) if pnls else 0.0,
        "avg_win": round(statistics.mean(wins), 4) if wins else 0.0,
        "avg_loss": round(statistics.mean(losses), 4) if losses else 0.0,
        "best": round(max(pnls), 2) if pnls else 0.0,
        "worst": round(min(pnls), 2) if pnls else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
    }


# ---------------------------------------------------------------------------
# Routen
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("dashboard.html",
                           start_capital=config.START_CAPITAL,
                           quote=config.QUOTE_ASSET)


@app.route("/api/stats")
def api_stats():
    trades = db.get_all_trades()
    closed = _apply_filters(_closed(trades))     # Kennzahlen folgen dem Filter
    open_trades = [t for t in trades if t["status"] == "open"]
    realized = db.realized_pnl()                 # Equity bleibt kumulativ (Kontostand)
    equity = config.START_CAPITAL + realized

    s = _summary(closed)
    s.update({
        "equity": round(equity, 2),
        "start_capital": config.START_CAPITAL,
        "return_pct": round((equity / config.START_CAPITAL - 1) * 100, 2),
        "open_count": len(open_trades),
        "total_trades": len(trades),
    })
    return jsonify(s)


@app.route("/api/equity")
def api_equity():
    curve = db.get_equity_curve()
    rng = request.args.get("range")
    if rng in RANGE_SECONDS:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=RANGE_SECONDS[rng])
        curve = [r for r in curve
                 if (dt := _parse_iso(r["ts"])) is not None and dt >= cutoff]
    return jsonify({
        "labels": [r["ts"] for r in curve],
        "values": [round(r["equity"], 2) for r in curve],
    })


@app.route("/api/open")
def api_open():
    open_trades = [t for t in db.get_all_trades() if t["status"] == "open"]
    return jsonify(open_trades)


@app.route("/api/trades")
def api_trades():
    side = request.args.get("side")
    symbol = request.args.get("symbol")
    reason = request.args.get("reason")
    result = request.args.get("result")  # 'win' | 'loss'

    trades = _apply_filters(_closed(db.get_all_trades()))
    if side:
        trades = [t for t in trades if t["side"] == side]
    if symbol:
        trades = [t for t in trades if t["symbol"] == symbol]
    if reason:
        trades = [t for t in trades if t["exit_reason"] == reason]
    if result == "win":
        trades = [t for t in trades if t["pnl"] > 0]
    elif result == "loss":
        trades = [t for t in trades if t["pnl"] <= 0]

    # entry_reasons / indicators sind JSON-Strings -> für die Anzeige parsen
    for t in trades:
        try:
            t["entry_reasons"] = json.loads(t["entry_reasons"]) if t["entry_reasons"] else []
        except (TypeError, json.JSONDecodeError):
            t["entry_reasons"] = []
    return jsonify(trades[:500])


@app.route("/api/analysis")
def api_analysis():
    closed = _apply_filters(_closed(db.get_all_trades()))

    # nach Richtung
    by_side = {}
    for sd in ("long", "short"):
        sub = [t for t in closed if t["side"] == sd]
        by_side[sd] = _summary(sub)

    # nach Exit-Grund
    by_reason = {}
    reasons = defaultdict(list)
    for t in closed:
        reasons[t["exit_reason"]].append(t)
    for r, sub in reasons.items():
        by_reason[r] = _summary(sub)

    # nach Coin (Top 15 nach Anzahl)
    sym_groups = defaultdict(list)
    for t in closed:
        sym_groups[t["symbol"]].append(t)
    by_symbol = []
    for sym, sub in sym_groups.items():
        d = _summary(sub)
        d["symbol"] = sym
        by_symbol.append(d)
    by_symbol.sort(key=lambda x: x["count"], reverse=True)
    by_symbol = by_symbol[:15]

    # nach Einstiegs-RVOL-Bucket (relatives Volumen beim Einstieg, Haupt-Zeitrahmen)
    # -> zeigt, ob Trades bei stärkerem Volumen-Surge besser laufen.
    rvol_buckets = defaultdict(list)
    for t in closed:
        try:
            snap = json.loads(t["indicators"])
            rvol = snap.get(config.PRIMARY_TIMEFRAME, {}).get("rvol")
        except (TypeError, json.JSONDecodeError, AttributeError):
            rvol = None
        if rvol is None:
            continue
        lo = int(rvol * 2) / 2.0   # auf 0,5er-Schritte abrunden
        bucket = f"{lo:.1f}-{lo + 0.5:.1f}"
        rvol_buckets[bucket].append(t)
    by_rvol = []
    for b, sub in sorted(rvol_buckets.items(), key=lambda x: float(x[0].split("-")[0])):
        d = _summary(sub)
        d["bucket"] = b
        by_rvol.append(d)

    # nach Einstiegs-Score-Bucket
    score_buckets = defaultdict(list)
    for t in closed:
        sc = t["entry_score"] or 0
        bucket = f"{int(sc)}-{int(sc)+1}"
        score_buckets[bucket].append(t)
    by_score = []
    for b, sub in sorted(score_buckets.items(), key=lambda x: int(x[0].split("-")[0])):
        d = _summary(sub)
        d["bucket"] = b
        by_score.append(d)

    # nach Einstiegs-Tageszeit (lokale Stunde 0-23) — zeigt, ob z.B. mittags
    # gehandelte Setups besser laufen als abends
    hour_buckets = defaultdict(list)
    for t in closed:
        h = t["entry_hour"]
        if h is None:
            continue
        hour_buckets[int(h)].append(t)
    by_hour = []
    for h in sorted(hour_buckets):
        d = _summary(hour_buckets[h])
        d["bucket"] = f"{h:02d}h"
        by_hour.append(d)

    # nach beitragender Strategie (ein Trade zählt für jede Strategie, die zu
    # seinem Einstieg beigetragen hat) -> "welche Strategie funktioniert wann"
    strat_groups = defaultdict(list)
    for t in closed:
        try:
            strs = json.loads(t["strategies"]) if t["strategies"] else []
        except (TypeError, json.JSONDecodeError):
            strs = []
        for s in strs:
            name = s.get("name") if isinstance(s, dict) else s
            if name:
                strat_groups[name].append(t)
    by_strategy = []
    for name, sub in strat_groups.items():
        d = _summary(sub)
        d["strategy"] = name
        by_strategy.append(d)
    by_strategy.sort(key=lambda x: x["total_pnl"], reverse=True)

    return jsonify({
        "by_side": by_side,
        "by_reason": by_reason,
        "by_symbol": by_symbol,
        "by_rvol": by_rvol,
        "by_score": by_score,
        "by_hour": by_hour,
        "by_strategy": by_strategy,
    })


@app.route("/api/symbols")
def api_symbols():
    """Liste aller Coins, für die es Trades gibt (für die Chart-Auswahl)."""
    trades = db.get_all_trades()
    groups = defaultdict(lambda: {"total": 0, "open": 0, "closed": 0})
    for t in trades:
        g = groups[t["symbol"]]
        g["total"] += 1
        if t["status"] == "open":
            g["open"] += 1
        else:
            g["closed"] += 1
    out = [{"symbol": s, **g} for s, g in groups.items()]
    out.sort(key=lambda x: (x["open"], x["total"]), reverse=True)
    return jsonify(out)


@app.route("/api/chart")
def api_chart():
    """Candlestick-Daten eines Coins plus alle zugehörigen Trades.

    Liefert die OHLC-Candles (für die Realität) und die Ein-/Ausstiege,
    damit das Frontend Entry, Stop, Take-Profit und offene Positionen
    direkt in den Kursverlauf einzeichnen kann.
    """
    symbol = request.args.get("symbol")
    interval = request.args.get("interval") or config.PRIMARY_TIMEFRAME
    if not symbol:
        return jsonify({"error": "symbol fehlt"}), 400
    try:
        limit = max(50, min(int(request.args.get("limit", 300)), 1000))
    except (TypeError, ValueError):
        limit = 300

    try:
        df = bx.get_klines(symbol, interval, limit)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502

    candles = [
        {
            "x": int(row.open_time.timestamp() * 1000),
            "o": round(float(row.open), 8),
            "h": round(float(row.high), 8),
            "l": round(float(row.low), 8),
            "c": round(float(row.close), 8),
        }
        for row in df.itertuples()
    ]

    keep = ("id", "side", "status", "entry_time", "entry_price", "qty",
            "stop_loss", "take_profit", "entry_score", "exit_time",
            "exit_price", "exit_reason", "pnl", "pnl_pct", "hold_minutes")
    trades = [
        {k: t[k] for k in keep}
        for t in db.get_all_trades()
        if t["symbol"] == symbol
    ]

    return jsonify({
        "symbol": symbol,
        "interval": interval,
        "candles": candles,
        "trades": trades,
    })


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Löscht ALLE Trades und den Equity-Verlauf. Unwiderruflich."""
    db.reset_all()
    return jsonify({"ok": True})


@app.route("/export.csv")
def export_csv():
    trades = db.get_all_trades()
    if not trades:
        return Response("keine Daten", mimetype="text/plain")

    # Indikator-Snapshot flach in Spalten ausrollen (pro TF/Key)
    fieldnames = [k for k in trades[0].keys() if k not in ("indicators",)]
    flat_rows = []
    extra_keys = set()
    for t in trades:
        row = {k: t[k] for k in fieldnames}
        try:
            snap = json.loads(t["indicators"]) if t["indicators"] else {}
            for tf, vals in snap.items():
                for key, v in vals.items():
                    col = f"{tf}_{key}"
                    row[col] = v
                    extra_keys.add(col)
        except (TypeError, json.JSONDecodeError):
            pass
        flat_rows.append(row)

    all_fields = fieldnames + sorted(extra_keys)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=all_fields, extrasaction="ignore")
    writer.writeheader()
    for row in flat_rows:
        writer.writerow(row)

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=cryptotrader_trades.csv"},
    )


if __name__ == "__main__":
    db.init_db()
    print(f"Dashboard läuft auf http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}")
    app.run(host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT, debug=False)
