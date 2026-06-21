"""
SQLite-Datenhaltung für Trades, Indikator-Snapshots und Equity-Verlauf.

Eine Tabelle `trades` speichert jeden (Paper-)Trade inklusive des kompletten
Indikator-Snapshots beim Einstieg (als JSON). Genau diese Daten dienen später
zur Analyse und zum Fine-Tuning der Strategie.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

import config

_local = threading.local()


def _conn():
    """Pro Thread eine eigene Verbindung (Bot + Dashboard laufen getrennt)."""
    if getattr(_local, "conn", None) is None:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL;")
        _local.conn = c
    return _local.conn


def init_db():
    c = _conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT    NOT NULL,
            side            TEXT    NOT NULL,           -- 'long' | 'short'
            status          TEXT    NOT NULL,           -- 'open' | 'closed'
            entry_time      TEXT    NOT NULL,
            entry_price     REAL    NOT NULL,
            qty             REAL    NOT NULL,
            notional        REAL    NOT NULL,
            stop_loss       REAL,
            take_profit     REAL,
            entry_score     REAL,
            long_score      REAL,
            short_score     REAL,
            primary_tf      TEXT,
            entry_reasons   TEXT,                       -- JSON-Liste
            indicators      TEXT,                       -- JSON-Snapshot beim Einstieg
            strategies      TEXT,                       -- JSON: beitragende Strategien [{name, points}]
            entry_hour      INTEGER,                     -- Stunde (0-23, lokale Zeit) des Einstiegs
            entry_weekday   INTEGER,                     -- Wochentag (0=Mo .. 6=So, lokale Zeit)
            leverage        REAL,                        -- simulierter Perp-Hebel
            exit_time       TEXT,
            exit_price      REAL,
            exit_reason     TEXT,                        -- 'take_profit'|'stop_loss'|'flow_flip'|'timeout'|'liquidation'
            fees            REAL DEFAULT 0,              -- Taker-Fees (Entry+Exit); Spread/Slippage stecken im Fill-Preis
            funding         REAL DEFAULT 0,             -- Funding-Kosten (signiert; Long zahlt bei positiver Rate)
            pnl             REAL,
            pnl_pct         REAL,
            hold_minutes    REAL
        );

        CREATE TABLE IF NOT EXISTS equity (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            equity      REAL NOT NULL,
            open_count  INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
        """
    )
    _migrate(c)
    c.commit()


def _migrate(c):
    """Nachträglich neue Spalten ergänzen, ohne bestehende Daten zu verlieren."""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(trades)").fetchall()}
    if "entry_hour" not in cols:
        c.execute("ALTER TABLE trades ADD COLUMN entry_hour INTEGER")
    if "entry_weekday" not in cols:
        c.execute("ALTER TABLE trades ADD COLUMN entry_weekday INTEGER")
    if "strategies" not in cols:
        c.execute("ALTER TABLE trades ADD COLUMN strategies TEXT")
    if "leverage" not in cols:
        c.execute("ALTER TABLE trades ADD COLUMN leverage REAL")
    if "funding" not in cols:
        c.execute("ALTER TABLE trades ADD COLUMN funding REAL DEFAULT 0")
    # Für Alt-Trades die Tageszeit aus entry_time (UTC) in lokale Zeit ableiten
    todo = c.execute(
        "SELECT id, entry_time FROM trades WHERE entry_hour IS NULL"
    ).fetchall()
    for r in todo:
        try:
            local = datetime.fromisoformat(r["entry_time"]).astimezone()
        except (TypeError, ValueError):
            continue
        c.execute(
            "UPDATE trades SET entry_hour=?, entry_weekday=? WHERE id=?",
            (local.hour, local.weekday(), r["id"]),
        )


def _now():
    return datetime.now(timezone.utc).isoformat()


def _local_time_parts():
    """Stunde (0-23) und Wochentag (0=Mo) in lokaler Zeit für die Tageszeit-Analyse."""
    local = datetime.now(timezone.utc).astimezone()
    return local.hour, local.weekday()


def open_trade(symbol, side, entry_price, qty, stop_loss, take_profit,
               entry_score, long_score, short_score, primary_tf,
               entry_reasons, indicators, fee, strategies=None, leverage=None):
    c = _conn()
    entry_hour, entry_weekday = _local_time_parts()
    cur = c.execute(
        """
        INSERT INTO trades (symbol, side, status, entry_time, entry_price, qty,
            notional, stop_loss, take_profit, entry_score, long_score, short_score,
            primary_tf, entry_reasons, indicators, strategies, leverage,
            entry_hour, entry_weekday, fees)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            symbol, side, "open", _now(), entry_price, qty,
            entry_price * qty, stop_loss, take_profit, entry_score,
            long_score, short_score, primary_tf,
            json.dumps(entry_reasons), json.dumps(indicators),
            json.dumps(strategies or []), leverage,
            entry_hour, entry_weekday, fee,
        ),
    )
    c.commit()
    return cur.lastrowid


def close_trade(trade_id, exit_price, exit_reason, pnl, pnl_pct,
                total_fees, hold_minutes, funding=0.0):
    c = _conn()
    c.execute(
        """
        UPDATE trades
        SET status='closed', exit_time=?, exit_price=?, exit_reason=?,
            pnl=?, pnl_pct=?, fees=?, funding=?, hold_minutes=?
        WHERE id=?
        """,
        (_now(), exit_price, exit_reason, pnl, pnl_pct, total_fees,
         funding, hold_minutes, trade_id),
    )
    c.commit()


def get_open_trades():
    c = _conn()
    rows = c.execute("SELECT * FROM trades WHERE status='open'").fetchall()
    return [dict(r) for r in rows]


def get_open_symbols():
    c = _conn()
    rows = c.execute("SELECT symbol FROM trades WHERE status='open'").fetchall()
    return {r["symbol"] for r in rows}


def count_open():
    c = _conn()
    return c.execute("SELECT COUNT(*) AS n FROM trades WHERE status='open'").fetchone()["n"]


def symbols_closed_since(cutoff_iso):
    """Coins, die seit `cutoff_iso` (ISO-Zeitstempel) zuletzt geschlossen wurden.
    Dient dem Wiedereinstiegs-Cooldown gegen schnelles Hin-und-Her-Traden."""
    c = _conn()
    rows = c.execute(
        "SELECT DISTINCT symbol FROM trades WHERE status='closed' AND exit_time >= ?",
        (cutoff_iso,),
    ).fetchall()
    return {r["symbol"] for r in rows}


def record_equity(equity_value, open_count):
    c = _conn()
    c.execute(
        "INSERT INTO equity (ts, equity, open_count) VALUES (?,?,?)",
        (_now(), equity_value, open_count),
    )
    c.commit()


def get_all_trades(limit=None):
    c = _conn()
    q = "SELECT * FROM trades ORDER BY id DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    return [dict(r) for r in c.execute(q).fetchall()]


def get_equity_curve():
    c = _conn()
    rows = c.execute("SELECT ts, equity FROM equity ORDER BY id ASC").fetchall()
    return [dict(r) for r in rows]


def realized_pnl():
    """Summe aller realisierten Gewinne/Verluste aus geschlossenen Trades."""
    c = _conn()
    r = c.execute(
        "SELECT COALESCE(SUM(pnl),0) AS s FROM trades WHERE status='closed'"
    ).fetchone()
    return r["s"]


def reset_all():
    """Löscht ALLE Trades und den Equity-Verlauf und setzt die IDs zurück.
    Unwiderruflich -> nur über den bewusst bestätigten Reset-Button im Dashboard."""
    c = _conn()
    c.executescript(
        """
        DELETE FROM trades;
        DELETE FROM equity;
        DELETE FROM sqlite_sequence WHERE name IN ('trades', 'equity');
        """
    )
    c.commit()
