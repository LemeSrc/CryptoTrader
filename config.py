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
TOP_N_SYMBOLS = 60            # Fokus auf die liquidesten Coins (engerer Spread, weniger Müll-Trades)
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
# Score-Schwelle für einen Einstieg. DEUTLICH angehoben: nur noch wenige,
# hochwertige Setups statt vieler Müll-Trades (Kosten fressen sonst alles).
ENTRY_SCORE_THRESHOLD = 20.0
ALLOW_LONG = True
ALLOW_SHORT = True

# --- Einstiegs-Filter (datengetrieben, verschärft) -------------------------
# Live-Auswertung (~930 Trades) zeigte: der Bot verlor v.a. durch (a) Übertraden
# (Gebühren!), (b) zu enge Stops und (c) toten Seitwärts-Chop. Diese Gates lassen
# nur noch klare Setups durch:
REQUIRE_TREND_ALIGNMENT = True   # Long nur im 4h-Aufwärtstrend, Short nur im Abwärtstrend
TREND_FILTER_TF = "4h"           # Zeitrahmen für die Trendrichtung (EMA50 vs EMA200)
MIN_ENTRY_ADX = 22.0             # Mindest-Trendstärke (ADX) auf dem Filter-Zeitrahmen
ADX_FILTER_TF = "15m"            # Zeitrahmen für die ADX-Prüfung
# Marktfluktuations-Filter: Mindest-Bandbreite (Bollinger) auf dem Haupt-Zeitrahmen.
# Blockt den toten "Hoch-runter-Chop" in engen Ranges (dort WR nur ~8-28 %).
MIN_BB_WIDTH = 0.022             # (bb_upper-bb_lower)/bb_mid; ~2,2 % Mindest-Bandbreite
BB_WIDTH_FILTER_TF = "15m"

# ---------------------------------------------------------------------------
# Strategie-Gewichte (Scoring)
# ---------------------------------------------------------------------------
# Jede benannte Strategie liefert Punkte für long/short. Der Beitrag wird mit
# dem hier gesetzten Gewicht multipliziert -> einzelne Strategien lassen sich
# verstärken (>1), abschwächen (<1) oder ganz abschalten (0), ohne Code zu
# ändern. Die beitragenden Strategien werden pro Trade gespeichert und sind im
# Dashboard auswertbar ("welche Strategie funktioniert wann am besten").
# Gewichte BEWUSST neutralisiert: Einzelstrategie-Performance kippte zwischen den
# Samples (z.B. Breakout mal bester, mal schlechtester) -> Overfitting-Falle. Die
# Profitabilität kommt jetzt aus den robusten FILTERN (Trend, ADX, Bandbreite,
# weite Stops, wenig Trades), nicht aus fein getunten Gewichten. Nur die neue
# Marktfluktuations-Strategie und die mehrtägigen Chart-Pattern werden betont,
# der schwächste Proxy abgeschaltet.
STRATEGY_WEIGHTS = {
    "Market Fluctuation":   1.4,   # NEU: Wick-Ablehnung an Extremen (dein Fokus)
    "Chart Patterns":       1.3,   # mehrtägige/-wöchige Formationen (robustes Konzept)
    "Reversal":             1.0,
    "Breakout":             1.0,
    "Trend Following":      1.0,
    "Moving Average Cross": 1.0,
    "Momentum":             1.0,
    "Ichimoku":             1.0,
    "VWAP":                 1.0,
    "Price Action":         1.0,
    "Heikin Ashi":          1.0,
    "Catalyst (approx)":    1.0,
    "Opening Range":        0.8,   # feuerte sehr oft & verlor in Summe am meisten
    "Order Flow (approx)":  0.8,
    "Volume Profile":       0.8,
    "Pivot Points":         0.8,
    "Mean Reversion":       0.8,
    "Fibonacci":            0.8,
    "MACD Divergence":      0.8,
    "Bollinger Squeeze":    0.6,
    "Range":                0.6,
    "Sentiment (approx)":   0.5,   # schwacher Proxy
    "Squeeze Play (approx)":0.0,   # in beiden Samples schlecht -> abgeschaltet
}

# Stop-Loss / Take-Profit auf Basis der ATR (Volatilität) des Haupt-Zeitrahmens
ATR_PERIOD = 14
# Stops bewusst WEITER (vorher 1.5 ATR -> 53 % wurden vom Rauschen ausgestoppt;
# Trades, die >60 min überlebten, hatten WR 35-56 %). Größeres TP für besseres R:R.
SL_ATR_MULTIPLIER = 2.0       # Stop-Loss-Abstand = 2.0 * ATR
TP_ATR_MULTIPLIER = 4.0       # Take-Profit-Abstand = 4.0 * ATR (Chance/Risiko = 2.0)
MAX_HOLD_HOURS = 48           # Position spätestens nach X Stunden schließen
# Gegensignal-Exits churnten Verluste (PF 0.15) -> aus. SL/TP/Timeout managen den Exit.
EXIT_ON_OPPOSITE_SIGNAL = False

# --- Anti-Churn (verhindert viele winzige Trades je Coin) ---
# Deutlich verlängert: Übertraden war der größte Verlustbringer (Gebühren).
REENTRY_COOLDOWN_MINUTES = 240   # Coin nach Close 4 h gesperrt
MIN_HOLD_MINUTES = 60            # frühester vorzeitiger Exit (falls Gegensignal aktiviert)

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
MIN_ENTRY_ADX = _ovr("MIN_ENTRY_ADX", float, MIN_ENTRY_ADX)
MIN_BB_WIDTH = _ovr("MIN_BB_WIDTH", float, MIN_BB_WIDTH)
SL_ATR_MULTIPLIER = _ovr("SL_ATR_MULTIPLIER", float, SL_ATR_MULTIPLIER)
TP_ATR_MULTIPLIER = _ovr("TP_ATR_MULTIPLIER", float, TP_ATR_MULTIPLIER)
REENTRY_COOLDOWN_MINUTES = _ovr("REENTRY_COOLDOWN_MINUTES", int, REENTRY_COOLDOWN_MINUTES)
ALLOW_LONG = _ovr_bool("ALLOW_LONG", ALLOW_LONG)
ALLOW_SHORT = _ovr_bool("ALLOW_SHORT", ALLOW_SHORT)
REQUIRE_TREND_ALIGNMENT = _ovr_bool("REQUIRE_TREND_ALIGNMENT", REQUIRE_TREND_ALIGNMENT)
EXIT_ON_OPPOSITE_SIGNAL = _ovr_bool("EXIT_ON_OPPOSITE_SIGNAL", EXIT_ON_OPPOSITE_SIGNAL)

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
