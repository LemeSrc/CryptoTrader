"""
Zentrale Konfiguration des Crypto Trading Bots.

Alle Werte können hier angepasst werden. Die Einstiegs-Bedingungen sind
bewusst NIEDRIG (locker) gehalten, damit viele Trades entstehen und später
im Dashboard analysiert werden können. Nach der Analyse können die Schwellen
erhöht / verfeinert werden.
"""

# ---------------------------------------------------------------------------
# Markt / Coins
# ---------------------------------------------------------------------------
QUOTE_ASSET = "USDT"          # Es werden nur Paare gegen diese Währung gehandelt
TOP_N_SYMBOLS = 120           # Anzahl der überwachten Coins (nach 24h-Volumen)
SYMBOL_REFRESH_MINUTES = 360  # Wie oft die Top-Coin-Liste neu geladen wird
# Coins, die immer ausgeschlossen werden (z.B. Stablecoin-Paare)
SYMBOL_BLACKLIST = {
    "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "DAIUSDT",
    "EURUSDT", "GBPUSDT", "AEURUSDT", "USDPUSDT", "USD1USDT", "XUSDUSDT",
}
# Tokens mit diesen Endungen ausschließen (gehebelte / spezielle Produkte)
SYMBOL_EXCLUDE_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

# ---------------------------------------------------------------------------
# Zeitrahmen (Candle-Intervalle)
# ---------------------------------------------------------------------------
# Der erste Eintrag ist der "Haupt"-Zeitrahmen für Ein-/Ausstieg & Preis.
# Die weiteren dienen als Trend-Bestätigung (Multi-Timeframe). Mit 1d kommt die
# übergeordnete Struktur dazu -> Grundlage für mehrtägige/-wöchige Chart-Pattern.
PRIMARY_TIMEFRAME = "15m"
TIMEFRAMES = ["15m", "1h", "4h", "1d"]
KLINE_LIMIT = 250             # Anzahl Candles pro Abfrage (1d*250 ~ 8 Monate Struktur)
# Auf diesen (höheren) Zeitrahmen werden mehrtägige/-wöchige Chart-Pattern
# gesucht (Doppeltop/-boden, Schulter-Kopf-Schulter, Dreiecke, Range-Breakout).
CHART_PATTERN_TFS = ("4h", "1d")

# ---------------------------------------------------------------------------
# Loop / Timing
# ---------------------------------------------------------------------------
SCAN_INTERVAL_SECONDS = 120   # Wie oft alle Coins gescannt werden (120 = 2 Min)
REQUEST_SLEEP_SECONDS = 0.15  # Pause zwischen API-Calls (höflich zu Binance)

# ---------------------------------------------------------------------------
# Paper-Trading / Kapital
# ---------------------------------------------------------------------------
START_CAPITAL = 500.0         # Virtuelles Startkapital (in USDT ~ 500 €)
RISK_PER_TRADE_PCT = 1.0      # Wie viel % des Kapitals pro Trade riskiert wird (SL-Abstand)
MAX_OPEN_POSITIONS = None     # None = keine Obergrenze (Cap abgeschafft)
MAX_POSITIONS_PER_SYMBOL = 1  # Pro Coin nur eine offene Position

# --- Handelskosten (werden voll auf jeden Fill angewandt) ---
TAKER_FEE_PCT = 0.10          # Gebühr pro Seite in % (Binance Spot Taker = 0.1%)
# Geschätzter Bid/Ask-Spread (Round-Trip, %). Je Seite wird die Hälfte als
# schlechterer Fill verrechnet (Kauf zum Ask, Verkauf zum Bid). Da wir nur
# Klines (kein Orderbuch) haben, ist dies eine konservative Pauschale; bei
# illiquideren Coins real eher höher.
SPREAD_PCT = 0.05

# ---------------------------------------------------------------------------
# Ein-/Ausstiegs-Logik
# ---------------------------------------------------------------------------
# Score-Schwelle für einen Einstieg. Viele benannte Strategien liefern je nach
# Zeitrahmen & Gewicht Punkte; der Gesamt-Score liegt dadurch höher als früher.
# Niedriger Wert => mehr Trades (bewusst moderat zum Datensammeln, tunebar).
ENTRY_SCORE_THRESHOLD = 6.0
ALLOW_LONG = True
ALLOW_SHORT = True

# --- Einstiegs-Filter (datengetrieben aus der Trade-Historie hergeleitet) ---
# Auswertung von ~900 geschlossenen Trades zeigte klar:
#  * Trades MIT dem höheren Trend (4h) liefern PF ~1.15, GEGEN den Trend PF ~0.9.
#  * In trendlosen Phasen (niedriger ADX) verlieren Einstiege deutlich.
# Daher nur noch mit dem 4h-Trend und nur bei ausreichender Trendstärke handeln.
REQUIRE_TREND_ALIGNMENT = True   # Long nur im 4h-Aufwärtstrend, Short nur im Abwärtstrend
TREND_FILTER_TF = "4h"           # Zeitrahmen für die Trendrichtung (EMA50 vs EMA200)
MIN_ENTRY_ADX = 20.0             # Mindest-Trendstärke (ADX) auf dem Filter-Zeitrahmen
ADX_FILTER_TF = "15m"            # Zeitrahmen für die ADX-Prüfung

# ---------------------------------------------------------------------------
# Strategie-Gewichte (Scoring)
# ---------------------------------------------------------------------------
# Jede benannte Strategie liefert Punkte für long/short. Der Beitrag wird mit
# dem hier gesetzten Gewicht multipliziert -> einzelne Strategien lassen sich
# verstärken (>1), abschwächen (<1) oder ganz abschalten (0), ohne Code zu
# ändern. Die beitragenden Strategien werden pro Trade gespeichert und sind im
# Dashboard auswertbar ("welche Strategie funktioniert wann am besten").
# Gewichte aus der Trade-Historie kalibriert (PF = Profit-Faktor je Strategie):
#   stark profitabel -> hoch, dauerhaft verlustreich -> runter.
STRATEGY_WEIGHTS = {
    "Breakout":             1.6,   # bester Ausreißer (PF ~1.25) -> hochgewichtet
    "Chart Patterns":       1.8,   # mehrtägige/-wöchige Formationen (PF ~1.20)
    "Reversal":             1.4,   # Stochastik-/Pattern-Umkehr (PF ~1.35)
    "Catalyst (approx)":    1.1,   # Volumen-/Volatilitäts-Spike (PF ~1.20)
    "Opening Range":        1.1,   # Tages-Opening-Range-Breakout (PF ~1.11)
    "Price Action":         1.1,   # Engulfing/Hammer/Inside-Bar
    "Order Flow (approx)":  1.0,   # Taker-Buy-Volumen (Proxy, PF ~1.07)
    "Heikin Ashi":          1.0,   # geglätteter HA-Trend
    "Trend Following":      1.0,   # EMA-Stapel / ADX-Richtung (jetzt per Gate erzwungen)
    "Moving Average Cross": 1.0,   # EMA9/EMA21- und MACD-Kreuze
    "Momentum":             1.0,   # ROC / MACD-Histogramm
    "Ichimoku":             1.0,   # Wolke + Tenkan/Kijun
    "VWAP":                 1.0,   # Lage zum VWAP + Re-Cross
    "Volume Profile":       0.8,   # Lage zum POC
    "Pivot Points":         0.8,   # Lage zu Pivot/R/S
    "Sentiment (approx)":   0.5,   # Kaufdruck-Trend (Proxy, kein News-Feed)
    "Mean Reversion":       0.5,   # RSI-Extreme verloren in den Daten -> abgewertet
    "Bollinger Squeeze":    0.5,   # PF ~0.75 -> abgewertet
    "Fibonacci":            0.4,   # PF ~0.75 -> abgewertet
    "MACD Divergence":      0.4,   # PF ~0.76 -> stark abgewertet (war zuvor am höchsten!)
    "Squeeze Play (approx)":0.3,   # PF ~0.42 -> Proxy, kaum tragfähig
    "Range":                0.3,   # PF ~0.24 -> schlechteste Strategie
}

# Stop-Loss / Take-Profit auf Basis der ATR (Volatilität) des Haupt-Zeitrahmens
ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.5       # Stop-Loss-Abstand = 1.5 * ATR
TP_ATR_MULTIPLIER = 2.5       # Take-Profit-Abstand = 2.5 * ATR (Chance/Risiko ~1.67)
MAX_HOLD_HOURS = 48           # Position spätestens nach X Stunden schließen
EXIT_ON_OPPOSITE_SIGNAL = True  # Bei klarem Gegensignal vorzeitig schließen

# --- Anti-Churn (verhindert viele winzige Trades je Coin) ---
# Nach dem Schließen einer Position bleibt der Coin für diese Dauer gesperrt,
# damit nicht sofort (oft in dieselbe Verlustrichtung) neu eingestiegen wird.
REENTRY_COOLDOWN_MINUTES = 90
# Erst nach dieser Mindesthaltedauer darf ein Gegensignal einen vorzeitigen
# Ausstieg auslösen -> kein Flip-Flop im 1-Candle-Takt. SL/TP greifen immer.
MIN_HOLD_MINUTES = 45

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "trades.db")
LOG_PATH = os.path.join(DATA_DIR, "bot.log")

# Dashboard
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 5000

# ---------------------------------------------------------------------------
# Laufzeit-Overrides per Umgebungsvariablen
# ---------------------------------------------------------------------------
# Praktisch beim Cloud-Hosting: Werte direkt in der Plattform-UI (Env Vars)
# ändern, ohne Code neu zu deployen. Beispiel: CT_START_CAPITAL=1000
def _ovr(name, cast, current):
    raw = os.environ.get("CT_" + name)
    if raw is None:
        return current
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return current


def _ovr_bool(name, current):
    raw = os.environ.get("CT_" + name)
    if raw is None:
        return current
    return raw.strip().lower() in ("1", "true", "yes", "on")


START_CAPITAL = _ovr("START_CAPITAL", float, START_CAPITAL)
SCAN_INTERVAL_SECONDS = _ovr("SCAN_INTERVAL_SECONDS", int, SCAN_INTERVAL_SECONDS)
ENTRY_SCORE_THRESHOLD = _ovr("ENTRY_SCORE_THRESHOLD", float, ENTRY_SCORE_THRESHOLD)
TOP_N_SYMBOLS = _ovr("TOP_N_SYMBOLS", int, TOP_N_SYMBOLS)
SPREAD_PCT = _ovr("SPREAD_PCT", float, SPREAD_PCT)
ALLOW_LONG = _ovr_bool("ALLOW_LONG", ALLOW_LONG)
ALLOW_SHORT = _ovr_bool("ALLOW_SHORT", ALLOW_SHORT)
REQUIRE_TREND_ALIGNMENT = _ovr_bool("REQUIRE_TREND_ALIGNMENT", REQUIRE_TREND_ALIGNMENT)

# Hosting: Plattformen geben den Port via $PORT vor; Bind-Host via CT_HOST=0.0.0.0
DASHBOARD_PORT = int(os.environ.get("PORT", DASHBOARD_PORT))
DASHBOARD_HOST = os.environ.get("CT_HOST", DASHBOARD_HOST)
# Optionaler abweichender DB-Pfad (z.B. persistentes Volume in der Cloud)
DB_PATH = os.environ.get("CT_DB_PATH", DB_PATH)
# Optionaler Passwortschutz fürs Dashboard. Wird CT_DASHBOARD_PASSWORD gesetzt,
# verlangen ALLE Dashboard-Routen HTTP-Basic-Auth -> Pflicht bei öffentlichem
# Hosting (sonst könnte jeder den Reset-Button auslösen). Leer = offen (lokal).
DASHBOARD_USER = os.environ.get("CT_DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.environ.get("CT_DASHBOARD_PASSWORD", "")
