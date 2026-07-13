"""
Datengetriebene Entry-Regeln ("gelernt" aus den eigenen Trade-Exporten).

Diese Regeln stammen NICHT aus Trading-Logik, sondern ausschließlich aus der
Auswertung der eigenen geschlossenen Trades (bewusst so gewollt: die Daten
entscheiden, nicht die Theorie). Sie werden als zusätzliches Gate NACH den
regulären Volume-Gates angewandt.

Basis v1 (2026-07-13): Export "cryptotrader_trades (5).csv",
509 geschlossene Trades vom 23.06.–13.07.2026, gesammelt mit
INVERT_SIGNALS=1 + STRUCTURAL_STOPS=1 (die Regeln gelten für GENAU diese
Konfiguration!). Greedy-Vorwärtssuche über Kandidaten-Filter:
    ungefiltert:  509 Trades, PnL −328 €, WR 43,4 %
    gefiltert:    129 Trades, PnL +279 €, WR 60,5 %
    stabil in beiden Zeithälften (H1 +173 € / H2 +105 €).

WICHTIG für die Zukunft: Nach jedem größeren Datenexport neu prüfen und die
Konstanten hier versioniert nachziehen (RULES_VERSION erhöhen). Abschaltbar
per CT_DATA_RULES=0.
"""

from datetime import datetime

import config

RULES_VERSION = 1

# --- Regel 1: Taker-Buy-Band (1m) -------------------------------------------
# Quartils-Auswertung zeigte: moderater Kaufdruck (0,48–0,65) = +125 € / 52 %,
# sehr niedriger (<=0,35) = leicht positiv; die Bereiche dazwischen und
# darüber (Extreme) verlieren massiv (je ~ −225 €).
TBR_LOW_MAX = 0.35            # erlaubt, wenn taker_buy_ratio <= diesem Wert ...
TBR_BAND = (0.48, 0.65)       # ... oder innerhalb dieses Bandes

# --- Regel 2: Stunden-Block (Serverzeit = UTC auf der VM) --------------------
# Stark negative Einstiegs-Stunden (je −18 bis −70 € bei n>=15).
BLOCKED_HOURS = {0, 1, 3, 4, 5, 7, 8, 20, 23}

# --- Regel 3: Symbol-Blocklist (datenbasiert) --------------------------------
# Coins mit Gesamt-PnL < −15 € bei n>=8 Trades im Sample.
BLOCKED_SYMBOLS = {
    "DRAMUSDT", "EWYUSDT", "HYPEUSDT", "KORUUSDT", "LABUSDT",
    "SKHYNIXUSDT", "SNDKUSDT", "SYNUSDT", "VELVETUSDT", "WLDUSDT",
}

# --- Regel 4: Score-Deckel ---------------------------------------------------
# Kontraintuitiv, aber klar in den Daten: sehr hohe Confluence-Scores (>5,5)
# waren das schlechteste Quartil (−157 €). Vermutlich "alle schreien in eine
# Richtung" = Erschöpfung. Die Daten entscheiden.
SCORE_MAX = 5.5


def entry_allowed(analysis):
    """Prüft die gelernten Regeln. Rückgabe: (erlaubt: bool, grund: str)."""
    if not config.DATA_RULES:
        return True, "data_rules_off"

    # Regel 3: Symbol
    if analysis["symbol"] in BLOCKED_SYMBOLS:
        return False, f"symbol_block({analysis['symbol']})"

    # Regel 2: Stunde (lokale Serverzeit, identisch zu entry_hour in der DB)
    hour = datetime.now().astimezone().hour
    if hour in BLOCKED_HOURS:
        return False, f"hour_block({hour})"

    # Regel 1: Taker-Buy-Band
    tbr = analysis.get("taker_buy_ratio")
    if tbr is not None:
        ok = tbr <= TBR_LOW_MAX or (TBR_BAND[0] <= tbr <= TBR_BAND[1])
        if not ok:
            return False, f"tbr_band({tbr:.2f})"

    # Regel 4: Score-Deckel
    if analysis["score"] > SCORE_MAX:
        return False, f"score_max({analysis['score']:.1f})"

    return True, "ok"
