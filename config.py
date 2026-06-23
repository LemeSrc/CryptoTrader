"""
Zentrale Konfiguration des Volume-/Order-Flow-Trading-Bots.

Dieser Bot ist bewusst auf VOLUMEN spezialisiert (Volume Profile + Order Flow
+ wenige volumennahe Kennzahlen). Er handelt PAPER auf echten Binance-USDT-M-
Futures-Daten mit simuliertem Hebel und vollständigem Echtgeld-Kostenmodell
(Taker-Fee, Spread, Slippage, Funding). Ziel: wenige, hochwertige, profitable
Trades — keine fixe Risk-to-Reward-/Winrate-Vorgabe.

Alle wichtigen Werte sind per CT_*-Umgebungsvariablen ohne Code-Deploy
überschreibbar (siehe unten).
"""

import os

# ---------------------------------------------------------------------------
# Markt / Coins
# ---------------------------------------------------------------------------
QUOTE_ASSET = "USDT"
# True = USDT-M Futures (Perp-Preise, Funding, Hebel — Standard für diesen Bot).
# False = Spot (kein Funding, kein nativer Short) als Fallback bei API-Sperre.
USE_FUTURES = True

# Zwei-Stufen-Coin-Auswahl ("nur die relevantesten Coins"):
#   1) Basis-Universe = Top-N liquideste Perps nach 24h-Quote-Volumen.
#   2) Hot-List = daraus die Coins mit dem stärksten LIVE-Volumen-Surge (RVOL).
# Tief gescannt wird nur die Hot-List -> schnell + dort, wo gerade Volumen ist.
BASE_UNIVERSE_N = 40
HOT_LIST_N = 12
SYMBOL_REFRESH_MINUTES = 60      # wie oft das Basis-Universe neu geladen wird
HOTLIST_REFRESH_SECONDS = 60     # wie oft die Hot-List (RVOL-Ranking) neu bestimmt wird

# Stablecoin-/Sonderprodukt-Paare immer ausschließen
SYMBOL_BLACKLIST = {
    "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "DAIUSDT",
    "EURUSDT", "GBPUSDT", "AEURUSDT", "USDPUSDT", "USD1USDT", "XUSDUSDT",
}
SYMBOL_EXCLUDE_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

# ---------------------------------------------------------------------------
# Zeitrahmen (Scalping-lastig: ~70 % 1m, ~30 % 5m-Kontext)
# ---------------------------------------------------------------------------
PRIMARY_TIMEFRAME = "1m"         # Order-Flow + Ein-/Ausstieg
CONTEXT_TF = "5m"                # Intraday-Kontext + Volume-Profil-Struktur
TIMEFRAMES = ["1m", "5m"]        # pro Coin werden nur diese 2 Klines geholt
PROFILE_TF = "5m"                # Zeitrahmen, auf dem POC/VAH/VAL bestimmt werden
KLINE_LIMIT = 300                # Candles je Abfrage (5m*300 ~ 25 h Struktur)

# ---------------------------------------------------------------------------
# Loop / Timing  ("Abfrage so schnell/oft wie möglich")
# ---------------------------------------------------------------------------
SCAN_INTERVAL_SECONDS = 20       # Hot-List wird alle 20 s gescannt
REQUEST_SLEEP_SECONDS = 0.06     # Pause zwischen API-Calls (höflich, aber zügig)

# ---------------------------------------------------------------------------
# Kapital / Hebel / Sizing
# ---------------------------------------------------------------------------
START_CAPITAL = 500.0            # virtuelles Startkapital (USDT ~ 500 €)
LEVERAGE = 3.0                   # Perp-Hebel (500 € steuern bis 1.500 € Notional)
# Obergrenze für den Verlust am Schutz-Stop in % des Equity. Dient als Risiko-
# CAP für das gewinnziel-basierte Sizing (s.u.) — verhindert Übergröße bei
# weiten Stops.
RISK_PER_TRADE_PCT = 3.0
MAX_OPEN_POSITIONS = 1           # nur 1 Position -> volle Margin pro Trade (größere Trades)
MAX_POSITIONS_PER_SYMBOL = 1
# Wartungsmarge für die Liquidationspreis-Schätzung (Binance Low-Tier ~0,4-0,5 %)
MAINTENANCE_MARGIN_PCT = 0.5

# Mindest-Gewinn pro Trade (USDT). Die Positionsgröße wird so gewählt, dass das
# Erreichen des Volume-Node-Ziels netto ~diesen Betrag bringt; Setups, deren Ziel
# zu nah liegt, um das zu schaffen, werden übersprungen (-> wenige, große Trades).
MIN_PROFIT_USDT = 20.0

# ---------------------------------------------------------------------------
# Echtgeld-Kostenmodell (voll auf jeden Trade angewandt)
# ---------------------------------------------------------------------------
# Futures-Taker-Fee je Seite (Binance USDT-M Taker = 0,04 %). Bei Spot 0,10 %.
TAKER_FEE_PCT = 0.04
# Geschätzter Round-Trip-Spread (%). Hälfte je Seite als schlechterer Fill.
SPREAD_PCT = 0.03
# Zusätzliche Slippage (%) für Marktorders. Basiswert; wird im Bot nach
# Liquidität/RVOL skaliert (illiquide/heiß = mehr Slippage).
SLIPPAGE_PCT = 0.02
# Funding (Perp): Default-Rate je 8 h, falls die reale Rate nicht abrufbar ist.
# Real wird sie pro Symbol aus /fapi/v1/premiumIndex geladen. Kosten beim Close:
#   funding = notional * rate * (hold_hours / 8)
FUNDING_RATE_DEFAULT_PCT_PER_8H = 0.01

# ---------------------------------------------------------------------------
# Volume-Gates (Selektivität -> WENIGE, hochwertige Trades)
# ---------------------------------------------------------------------------
ALLOW_LONG = True
ALLOW_SHORT = True
# TEST-Schalter: kehrt jedes Signal um (aus Long wird Short und umgekehrt).
# Hypothese-Test "die Richtung ist systematisch verkehrt". Wirkt konsistent auf
# Einstieg, Ziel/Stop-Richtung UND den Order-Flow-Flip-Exit. Per CT_INVERT_SIGNALS=1.
INVERT_SIGNALS = False
# Confluence-Score-Schwelle (mehrere Volumen-Signale müssen zusammenkommen).
ENTRY_SCORE_THRESHOLD = 2.5
# RVOL-Gate: nur handeln, wenn das Volumen klar erhöht ist (echter Surge),
# nie im toten Seitwärts-Volumen.
MIN_RVOL = 1.8
# Liquiditäts-Floor: Mindest-24h-Quote-Volumen (begrenzt Spread/Slippage).
MIN_QUOTE_VOL_24H = 50_000_000
# Order-Flow-Gate: Mindest-|Delta-Anteil| (|CVD-Delta|/Volumen) in Richtung.
MIN_TAKER_IMBALANCE = 0.06       # ~ taker_buy_ratio >0,56 bzw. <0,44
# Kosten-Gate: erwarteter Weg zum nächsten Volume-Node muss die geschätzten
# Round-Trip-Kosten um diesen Faktor übersteigen, sonst kein Trade.
MIN_EDGE_COST_RATIO = 1.5

# Volume-Profile-Parameter
VP_LOOKBACK = 240                # Candles fürs Profil (auf PROFILE_TF)
VP_BINS = 30                     # Preis-Bins
VALUE_AREA_PCT = 0.70            # Anteil Volumen in der Value Area (VAH..VAL)
# "Nähe" zu einem Level in ATR-Vielfachen (Location-Gate / Node-Erkennung)
NEAR_NODE_ATR = 0.5

# ---------------------------------------------------------------------------
# Stops / Ziele / Exits (volumen-nativ, keine fixe R:R)
# ---------------------------------------------------------------------------
ATR_PERIOD = 14
# Schutz-Stop (katastrophal, Kapitalerhalt) als ATR-Vielfaches des Haupt-TF.
# Bewusst klar innerhalb der 3x-Liquidation (~ -33 %).
STOP_ATR_MULT = 1.2
# Fallback-Ziel, falls kein Volume-Node in Trade-Richtung voraus liegt.
TP_ATR_MULT = 2.0
# Primär-Exit: schließen, sobald der Order-Flow (Delta/CVD) klar dreht.
ORDER_FLOW_FLIP_EXIT = True
MAX_HOLD_MINUTES = 90            # "Halten nicht über Stunden"
MIN_HOLD_MINUTES = 2             # frühester Flip-Exit (Anti-Churn im Sekundentakt)
REENTRY_COOLDOWN_MINUTES = 30    # Coin nach Close gesperrt (Anti-Churn)

# Sicherheits-Cap: Schutz-Stop nie weiter als dieser %-Wert vom Entry
# (verhindert versehentliche Nähe zur Liquidation bei Volatilitäts-Spikes).
MAX_STOP_PCT = 100.0 / LEVERAGE * 0.5    # = halber Abstand zur Liquidation

# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "trades.db")
LOG_PATH = os.path.join(DATA_DIR, "bot.log")

# Dashboard
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 5000

# ---------------------------------------------------------------------------
# Laufzeit-Overrides per Umgebungsvariablen (CT_*)
# ---------------------------------------------------------------------------
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


USE_FUTURES = _ovr_bool("USE_FUTURES", USE_FUTURES)
START_CAPITAL = _ovr("START_CAPITAL", float, START_CAPITAL)
LEVERAGE = _ovr("LEVERAGE", float, LEVERAGE)
RISK_PER_TRADE_PCT = _ovr("RISK_PER_TRADE_PCT", float, RISK_PER_TRADE_PCT)
MIN_PROFIT_USDT = _ovr("MIN_PROFIT_USDT", float, MIN_PROFIT_USDT)
MAX_OPEN_POSITIONS = _ovr("MAX_OPEN_POSITIONS", int, MAX_OPEN_POSITIONS)
SCAN_INTERVAL_SECONDS = _ovr("SCAN_INTERVAL_SECONDS", int, SCAN_INTERVAL_SECONDS)
BASE_UNIVERSE_N = _ovr("BASE_UNIVERSE_N", int, BASE_UNIVERSE_N)
HOT_LIST_N = _ovr("HOT_LIST_N", int, HOT_LIST_N)
ENTRY_SCORE_THRESHOLD = _ovr("ENTRY_SCORE_THRESHOLD", float, ENTRY_SCORE_THRESHOLD)
MIN_RVOL = _ovr("MIN_RVOL", float, MIN_RVOL)
MIN_QUOTE_VOL_24H = _ovr("MIN_QUOTE_VOL_24H", float, MIN_QUOTE_VOL_24H)
MIN_TAKER_IMBALANCE = _ovr("MIN_TAKER_IMBALANCE", float, MIN_TAKER_IMBALANCE)
MIN_EDGE_COST_RATIO = _ovr("MIN_EDGE_COST_RATIO", float, MIN_EDGE_COST_RATIO)
TAKER_FEE_PCT = _ovr("TAKER_FEE_PCT", float, TAKER_FEE_PCT)
SPREAD_PCT = _ovr("SPREAD_PCT", float, SPREAD_PCT)
SLIPPAGE_PCT = _ovr("SLIPPAGE_PCT", float, SLIPPAGE_PCT)
FUNDING_RATE_DEFAULT_PCT_PER_8H = _ovr(
    "FUNDING_RATE_DEFAULT_PCT_PER_8H", float, FUNDING_RATE_DEFAULT_PCT_PER_8H)
STOP_ATR_MULT = _ovr("STOP_ATR_MULT", float, STOP_ATR_MULT)
TP_ATR_MULT = _ovr("TP_ATR_MULT", float, TP_ATR_MULT)
MAX_HOLD_MINUTES = _ovr("MAX_HOLD_MINUTES", int, MAX_HOLD_MINUTES)
REENTRY_COOLDOWN_MINUTES = _ovr("REENTRY_COOLDOWN_MINUTES", int, REENTRY_COOLDOWN_MINUTES)
ALLOW_LONG = _ovr_bool("ALLOW_LONG", ALLOW_LONG)
ALLOW_SHORT = _ovr_bool("ALLOW_SHORT", ALLOW_SHORT)
INVERT_SIGNALS = _ovr_bool("INVERT_SIGNALS", INVERT_SIGNALS)
ORDER_FLOW_FLIP_EXIT = _ovr_bool("ORDER_FLOW_FLIP_EXIT", ORDER_FLOW_FLIP_EXIT)

# Spot-Fallback hat höhere Taker-Fee -> automatisch anheben, falls nicht
# ausdrücklich per CT_TAKER_FEE_PCT überschrieben.
if not USE_FUTURES and "CT_TAKER_FEE_PCT" not in os.environ:
    TAKER_FEE_PCT = 0.10
    ALLOW_SHORT = False           # Spot kann nicht nativ shorten

# Abgeleitete Größe nach möglichen Overrides neu berechnen
MAX_STOP_PCT = 100.0 / LEVERAGE * 0.5

# Hosting: Plattformen geben den Port via $PORT vor; Bind-Host via CT_HOST=0.0.0.0
DASHBOARD_PORT = int(os.environ.get("PORT", DASHBOARD_PORT))
DASHBOARD_HOST = os.environ.get("CT_HOST", DASHBOARD_HOST)
DB_PATH = os.environ.get("CT_DB_PATH", DB_PATH)
# Optionaler Passwortschutz fürs Dashboard (Pflicht bei öffentlichem Hosting).
DASHBOARD_USER = os.environ.get("CT_DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.environ.get("CT_DASHBOARD_PASSWORD", "")
