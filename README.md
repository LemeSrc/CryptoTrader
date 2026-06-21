# CryptoTrader — Volume-/Order-Flow-Bot (Paper-Modus)

Ein auf **Volumen** spezialisierter Trading-Bot, der mit echten Binance-**USDT-M-
Futures**-Daten **simulierte** (Paper-)Trades mit Hebel durchführt. Er sucht
Setups ausschließlich über Volumen: **Volume Profile** (POC/VAH/VAL) und
**Order Flow** (CVD/Delta) plus wenige volumennahe Kennzahlen (RVOL, VWAP, MFI,
OBV). Jeder Trade wird samt Volumen-Snapshot gespeichert und im Web-Dashboard
ausgewertet.

> ⚠️ **Kein echtes Geld.** Es werden ausschließlich öffentliche Binance-Endpunkte
> (`fapi.binance.com`) verwendet, es gibt keine API-Keys und keine echten Orders.
> Der Hebel und alle Kosten werden lediglich simuliert.

## Funktionsweise

- **Daten:** öffentliche Binance-USDT-M-Futures-API. Echte Perp-Preise,
  Futures-Taker-Volumen (Order-Flow) und reale **Funding-Rate**. Fallback auf
  Spot via `CT_USE_FUTURES=0`.
- **Coin-Auswahl (2-stufig):** Basis-Universe = Top-40 liquideste Perps →
  **Hot-List** = Top-12 nach LIVE-Volumen-Surge (RVOL). Tief gescannt wird nur
  die Hot-List → schnell und nur dort, wo gerade Volumen ist.
- **Zeitrahmen:** 1m (Order-Flow/Einstieg) + 5m (Kontext + Volume-Profil). Scan ~20 s.
- **Indikatoren (nur Volumen):** Volume Profile (POC/VAH/VAL/Nodes), CVD/Delta,
  RVOL, VWAP, MFI, OBV; ATR nur als Volatilitätsmaß für Stops/Slippage.
- **Signale (Confluence):** Value-Edge-Reversion (VAL/VAH), Volume-Breakout,
  POC-Acceptance, VWAP-Reaktion, CVD-Divergenz. Mehrere müssen zusammenkommen.
- **Gates (wenige, hochwertige Trades):** RVOL-Surge, echte Volumen-Location,
  Order-Flow-Agreement (nie gegen die Tape), Kosten-Gate (Weg zum Ziel muss die
  Round-Trip-Kosten schlagen), Wiedereinstiegs-Cooldown.
- **Hebel & Kosten (Echtgeld-realistisch):** 3x Perp. Taker-Fee, Spread,
  **Slippage** und **Funding** werden auf jeden Trade verrechnet;
  Liquidationspreis wird mitgeführt.
- **Exits (keine fixe R:R — nur profitabel):** Order-Flow-Flip (Primär-Exit),
  Struktur-Ziel am nächsten Volume-Node/POC, Schutz-Stop (innerhalb der
  Liquidation), Zeit-Stop (≤ 90 min).
- **Speicherung:** SQLite (`data/trades.db`) inkl. Volumen-Snapshot je Trade.

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
| `BASE_UNIVERSE_N` / `HOT_LIST_N` | Größe Basis-Universe / aktiv gescannte Hot-List |
| `TIMEFRAMES` / `PROFILE_TF` | Zeitrahmen (1m+5m) / Volume-Profil-TF |
| `START_CAPITAL` / `LEVERAGE` | Startkapital (500) / Perp-Hebel (3x) |
| `ENTRY_SCORE_THRESHOLD` | Confluence-Schwelle – **höher = weniger Trades** |
| `MIN_RVOL` | Mindest-Volumen-Surge fürs Einsteigen (zentrales Gate) |
| `MIN_TAKER_IMBALANCE` | Order-Flow-Agreement-Schwelle |
| `MIN_EDGE_COST_RATIO` | Kosten-Gate: Ziel-Weg muss Kosten × Faktor schlagen |
| `STOP_ATR_MULT` / `TP_ATR_MULT` | Schutz-Stop / Fallback-Ziel (ATR) |
| `TAKER_FEE_PCT` / `SPREAD_PCT` / `SLIPPAGE_PCT` | Handelskosten je Fill |
| `FUNDING_RATE_DEFAULT_PCT_PER_8H` | Funding-Default, falls Rate nicht abrufbar |
| `RISK_PER_TRADE_PCT` | Risiko pro Trade in % des Kapitals (am Schutz-Stop) |
| `MAX_OPEN_POSITIONS` | parallele Positionen (Margin wird geteilt) |
| `MAX_HOLD_MINUTES` / `REENTRY_COOLDOWN_MINUTES` | Halte-Cap / Anti-Churn |
| `SCAN_INTERVAL_SECONDS` | Scan-Takt der Hot-List (Standard 20s) |

> Viele Werte lassen sich auch per `CT_*`-Umgebungsvariablen überschreiben
> (praktisch beim Cloud-Hosting). Details: [`DEPLOYMENT.md`](DEPLOYMENT.md).

## Daten für die Analyse exportieren

Im Dashboard oben rechts **„Trades als CSV"** klicken. Die CSV enthält pro Trade
alle Volumen-Kennzahlen beim Einstieg (POC/VAH/VAL, RVOL, CVD, Taker-Ratio, …),
ausgerollt je Zeitrahmen – Grundlage fürs datengetriebene Nachjustieren der Gates.

## Tuning (weniger/mehr Trades)

Die Profitabilität kommt aus den **Gates**, nicht aus fein getunten Gewichten.
- **Zu wenige Trades?** `CT_MIN_RVOL` senken (z. B. 1.5), `CT_ENTRY_SCORE_THRESHOLD`
  senken, `CT_HOT_LIST_N` erhöhen.
- **Zu viele/teure Trades?** `CT_MIN_RVOL` und `CT_MIN_EDGE_COST_RATIO` anheben,
  `CT_REENTRY_COOLDOWN_MINUTES` erhöhen.
- Beim Wechsel vom alten Bot **einmal den Reset-Button im Dashboard** drücken
  (alte Spot-Trades & Equity verfälschen sonst die neue Auswertung).
