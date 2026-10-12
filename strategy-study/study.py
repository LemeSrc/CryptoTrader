"""Strategiestudie: einfache Handelsregeln auf historischen Kursen.

Aktien und Krypto werden getrennt gerechnet und nie vermischt. Die Studie
handelt nichts, sie rechnet nur zurueck.

Gegen Zufallsfunde gibt es drei feste Regeln:
- Die Daten werden geteilt. Die ersten 70 Prozent dienen der Suche, die
  letzten 30 Prozent nur der Pruefung.
- Gebuehren und Spanne werden bei jedem Kauf und Verkauf abgezogen.
- Eine Regel besteht nur, wenn sie in beiden Teilen besser ist als Kaufen und
  Halten und der Gewinn im Pruefteil kein Zufall sein duerfte (t ueber 2).

Aufruf:  python study.py
Braucht: pandas, yfinance
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data"
TRAIN_SHARE = 0.7

# shortcut: feste Titellisten von heute, bei Einzelwerten waere das
# Ueberlebensverzerrung. Deshalb bei Aktien nur breite ETFs. Bei Krypto sind es
# vier Muenzen, die es 2018 schon gab und heute noch gibt, das schoent das
# Ergebnis von Kaufen und Halten.
CLASSES = {
    "Aktien": {
        "symbols": ["SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV"],
        "start": "2010-01-01",
        "cost": 0.0005,  # je Kauf oder Verkauf
        "year": 252,
        "weekdays": 5,
        "has_close": True,
    },
    "Krypto": {
        "symbols": ["BTC-USD", "ETH-USD", "XRP-USD", "LTC-USD"],
        "start": "2018-01-01",
        "cost": 0.001,
        "year": 365,
        "weekdays": 7,
        "has_close": False,  # Markt rund um die Uhr, kein ueber Nacht
    },
}
TAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def load(symbol: str, start: str, hourly: bool = False) -> pd.DataFrame:
    path = DATA / f"{symbol}_{'1h' if hourly else '1d'}.csv"
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True)
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    # Yahoo gibt Stundenkurse nur fuer die letzten 730 Tage heraus.
    df = ticker.history(period="730d", interval="1h") if hourly else ticker.history(start=start)
    if df.empty:
        raise RuntimeError(f"keine Kurse fuer {symbol}")
    idx = df.index.tz_convert("UTC") if hourly else df.index
    df.index = idx.tz_localize(None)
    df = df[["Open", "Close"]].dropna()
    DATA.mkdir(exist_ok=True)
    df.to_csv(path)
    return df


def net(pos: pd.Series, ret: pd.Series, cost: float) -> pd.Series:
    """Ertrag je Zeitraum nach Kosten. pos gilt fuer denselben Zeitraum wie ret,
    muss also schon vorher festgestanden haben."""
    pos = pos.reindex(ret.index).fillna(0.0)
    change = pos.diff().abs().fillna(pos.abs())
    return (pos * ret.fillna(0.0)) - change * cost


def daily_rules(df: pd.DataFrame, cfg: dict) -> dict[str, tuple[pd.Series, int]]:
    close, open_ = df["Close"], df["Open"]
    cc = close.pct_change()
    cost = cfg["cost"]
    out: dict[str, tuple[pd.Series, int]] = {}

    def add(name: str, pos: pd.Series) -> None:
        pos = pos.astype(float).fillna(0.0)
        out[name] = (net(pos, cc, cost), int((pos.diff() > 0).sum()))

    def gestern(cond: pd.Series) -> pd.Series:
        # Ein Signal aus dem Schlusskurs gilt erst fuer den naechsten Tag.
        return cond.astype(float).shift(1)

    add("Kaufen und Halten", gestern(close > 0))
    for n in (50, 100, 200):
        add(f"Kurs ueber Schnitt {n} Tage", gestern(close > close.rolling(n).mean()))
    for n in (20, 60, 120):
        add(f"Gestiegen ueber {n} Tage", gestern(close.pct_change(n) > 0))
    for k in (2, 3):
        add(f"Nach {k} Verlusttagen 1 Tag halten", gestern((cc < 0).rolling(k).sum() == k))

    # Kalenderregeln stehen im Voraus fest, hier ist kein Verschieben noetig.
    wd = pd.Series(close.index.dayofweek, index=close.index)
    for d in range(cfg["weekdays"]):
        add(f"Nur {TAGE[d]}", wd == d)
    monat = close.groupby(close.index.to_period("M"))
    add("Monatswechsel", (monat.cumcount() < 3) | (monat.cumcount(ascending=False) == 0))

    if cfg["has_close"]:
        n = len(close) - 1
        out["Ueber Nacht (Schluss bis Eroeffnung)"] = (
            (open_ / close.shift(1) - 1 - 2 * cost).fillna(0.0), n,
        )
        out["Nur tagsueber (Eroeffnung bis Schluss)"] = (close / open_ - 1 - 2 * cost, n + 1)
    return out


def hourly_rules(df: pd.DataFrame, cfg: dict) -> dict[str, tuple[pd.Series, int]]:
    close = df["Close"]
    ret = close.pct_change()  # Zeile t ist die Stunde, die um t beginnt
    hour = pd.Series(close.index.hour, index=close.index)
    out: dict[str, tuple[pd.Series, int]] = {}

    def add(name: str, pos: pd.Series) -> None:
        pos = pos.astype(float)
        hourly = net(pos, ret, cfg["cost"])
        out[name] = (hourly.resample("D").sum(), int((pos.diff() > 0).sum()))

    add("Kaufen und Halten", hour >= 0)
    for h in range(0, 24, 4):
        add(f"Nur {h:02d} bis {h + 4:02d} Uhr UTC", (hour >= h) & (hour < h + 4))
    return out


def stats(r: pd.Series, year: int) -> dict[str, float]:
    r = r.dropna()
    sd = r.std()
    if len(r) < 2 or not sd or math.isnan(sd):
        return {"ertrag": 0.0, "sharpe": 0.0, "t": 0.0, "dd": 0.0}
    cum = (1 + r).cumprod()
    return {
        "ertrag": r.mean() * year * 100,
        "sharpe": r.mean() / sd * math.sqrt(year),
        "t": r.mean() / sd * math.sqrt(len(r)),
        "dd": (cum / cum.cummax() - 1).min() * 100,
    }


def evaluate(per_symbol: list[dict[str, tuple[pd.Series, int]]], year: int) -> pd.DataFrame:
    rows = []
    for name in per_symbol[0]:
        # Jeder Titel gleich gewichtet.
        r = pd.concat([s[name][0] for s in per_symbol], axis=1).mean(axis=1).dropna()
        cut = r.index[int(len(r) * TRAIN_SHARE)]
        such, pruef = stats(r[r.index < cut], year), stats(r[r.index >= cut], year)
        rows.append(
            {
                "Regel": name,
                "Trades": sum(s[name][1] for s in per_symbol),
                "Suche %/J": such["ertrag"],
                "Suche Sharpe": such["sharpe"],
                "Pruef %/J": pruef["ertrag"],
                "Pruef Sharpe": pruef["sharpe"],
                "Pruef t": pruef["t"],
                "Pruef max. Rueckgang %": pruef["dd"],
            }
        )
    table = pd.DataFrame(rows).set_index("Regel")
    base = table.loc["Kaufen und Halten"]
    table["Urteil"] = [
        "Vergleich" if name == "Kaufen und Halten"
        else "besteht" if (
            row["Suche Sharpe"] > base["Suche Sharpe"]
            and row["Pruef Sharpe"] > base["Pruef Sharpe"]
            and row["Pruef t"] > 2
        )
        else "faellt durch"
        for name, row in table.iterrows()
    ]
    return table.sort_values("Suche Sharpe", ascending=False)


def main() -> None:
    pd.set_option("display.width", 200)
    for klasse, cfg in CLASSES.items():
        sets = [("Tageskurse seit " + cfg["start"], False, daily_rules)]
        if not cfg["has_close"]:
            sets.append(("Stundenkurse, letzte 730 Tage", True, hourly_rules))
        for titel, hourly, rules in sets:
            per_symbol = [rules(load(s, cfg["start"], hourly), cfg) for s in cfg["symbols"]]
            table = evaluate(per_symbol, cfg["year"])
            print(f"\n=== {klasse}: {titel} ({', '.join(cfg['symbols'])}) ===")
            print(f"{len(table) - 1} Regeln geprueft, Kosten {cfg['cost'] * 100:.2f} % je Seite")
            print(table.to_string(float_format=lambda x: f"{x:.2f}"))


if __name__ == "__main__":
    main()
