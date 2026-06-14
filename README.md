# CryptoTrader Bot (Paper-Modus)

Ein Crypto-Trading-Bot, der mit echten Binance-Marktdaten **simulierte**
(Paper-)Trades auf vielen Coins durchführt. Er erkennt Ein-/Ausstiegszeitpunkte
über technische Indikatoren und Candlestick-Pattern, speichert jeden Trade samt
Indikator-Snapshot in einer Datenbank und stellt alles in einem Web-Dashboard
zur Analyse bereit.

> ⚠️ **Kein echtes Geld.** Es werden ausschließlich öffentliche Binance-Endpunkte
> verwendet, es gibt keine API-Keys und keine echten Orders. Ziel von Phase 1 ist
> das Sammeln von Daten, um die Strategie zu analysieren und fein zu justieren.

## Funktionsweise

- **Daten:** öffentliche Binance-API (`api.binance.com`), Top-Coins nach 24h-Volumen.
- **Zeitrahmen:** 15m (Haupt), 1h, 4h, 1d (Multi-Timeframe; 1d trägt die übergeordnete Struktur).
- **Indikatoren:** RSI, MACD, EMA(9/21/50/200), Bollinger/Keltner, Stochastik, ATR,
  ADX/DI, VWAP, Ichimoku, Donchian, ROC, OBV, Heikin-Ashi, Pivots, Fibonacci,
  Volume-Profile sowie Candlestick- und **mehrtägige Chart-Pattern** (Doppeltop/-boden,
  Schulter-Kopf-Schulter, Dreiecke, Range-Breakout).
- **Strategien & Scoring:** ~20 benannte Strategien geben gewichtete Punkte (Gewichte
  in `config.py`, datengetrieben kalibriert). Jeder Trade speichert seine beitragenden
  Strategien → im Dashboard nach Strategie auswertbar.
- **Einstiegs-Filter:** nur mit dem 4h-Trend und nur bei ausreichender Trendstärke (ADX)
  – aus der Trade-Historie hergeleitet.
- **Kosten:** Taker-Fee **und** Spread werden auf jeden Fill verrechnet.
- **Exit:** Take-Profit / Stop-Loss (ATR), Timeout oder Gegensignal – mit Mindesthalte-
  dauer und Wiedereinstiegs-Cooldown gegen Übertraden.
- **Speicherung:** SQLite (`data/trades.db`) inkl. komplettem Indikator-Snapshot.

## Installation

```powershell
cd C:\Users\lenny\CryptoTrader
& "C:\Users\lenny\AppData\Local\Programs\Python\Python314\python.exe" -m pip install -r requirements.txt
```

## Starten

**Bot** (sammelt Trades, läuft dauerhaft):
```powershell
& "C:\Users\lenny\AppData\Local\Programs\Python\Python314\python.exe" bot.py
```
oder Doppelklick auf `start_bot.bat`.

**Dashboard** (in einem zweiten Fenster):
```powershell
& "C:\Users\lenny\AppData\Local\Programs\Python\Python314\python.exe" dashboard.py
```
oder Doppelklick auf `start_dashboard.bat`, dann Browser: <http://127.0.0.1:5000>

**Rund um die Uhr online (ohne deinen PC):** kombinierter Start von Bot +
Dashboard in einem Prozess mit `python run_all.py`. Hosting-Optionen und
Schritt-für-Schritt-Anleitung in [`DEPLOYMENT.md`](DEPLOYMENT.md).

## Einstellungen

Alles in [`config.py`](config.py). Wichtige Werte:

| Einstellung | Bedeutung |
|---|---|
| `TOP_N_SYMBOLS` | Anzahl überwachter Coins (Standard 120) |
| `TIMEFRAMES` | Zeitrahmen-Liste (erster = Haupt) |
| `START_CAPITAL` | Startkapital (Standard 500) |
| `ENTRY_SCORE_THRESHOLD` | Einstiegs-Schwelle – **niedrig = viele Trades** |
| `REQUIRE_TREND_ALIGNMENT` / `MIN_ENTRY_ADX` | Trend- & Trendstärke-Filter (Einstiegs-Gates) |
| `STRATEGY_WEIGHTS` | Gewicht je Strategie (0 = aus) |
| `SL_ATR_MULTIPLIER` / `TP_ATR_MULTIPLIER` | Stop-Loss / Take-Profit Abstand |
| `TAKER_FEE_PCT` / `SPREAD_PCT` | Handelskosten je Fill |
| `RISK_PER_TRADE_PCT` | Risiko pro Trade in % des Kapitals |
| `MAX_OPEN_POSITIONS` | Max. offene Positionen (`None` = unbegrenzt) |
| `REENTRY_COOLDOWN_MINUTES` / `MIN_HOLD_MINUTES` | Anti-Churn-Sperren |
| `SCAN_INTERVAL_SECONDS` | Scan-Intervall (Standard 120s) |

> Viele Werte lassen sich auch per `CT_*`-Umgebungsvariablen überschreiben
> (praktisch beim Cloud-Hosting). Details: [`DEPLOYMENT.md`](DEPLOYMENT.md).

## Daten für die Analyse exportieren

Im Dashboard oben rechts **„Trades als CSV"** klicken. Die CSV enthält pro Trade
alle Indikatorwerte beim Einstieg (pro Zeitrahmen ausgerollt). Diese Datei kannst
du zur Feinjustierung der Indikatoren weitergeben.

## Phasenplan

1. **Sammeln (jetzt):** niedrige Schwellen, viele Trades, alles wird gespeichert.
2. **Analysieren:** Dashboard + CSV → welche Indikator-Kombinationen gewinnen?
3. **Fine-Tuning:** Schwellen/Gewichte in `config.py` / `strategy.py` anpassen.
4. **(Optional) Echt-Modus:** erst nach validierter, profitabler Strategie.
