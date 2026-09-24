# Risiko

Die Stelle, an der ein Copy-Bot ueberlebt oder stirbt. Die Datenquellen sind
Fleissarbeit, die Bewertung ist Statistik, aber hier entscheidet sich, ob ein
schlechter Monat ein schlechter Monat bleibt.

## Die Groesse kommt nie vom Vorbild

Eins zu eins bezieht sich auf Titel und Richtung, nicht auf den Betrag. Wer
einer Senatorin mit achtstelligem Depot in der Positionsgroesse folgt, ist nach
einem schlechten Monat fertig.

```
Risikobetrag = Kapital * risk_per_trade_pct * Ueberzeugungsfaktor
Stueckzahl   = Risikobetrag / Abstand zum Stop
```

Bei 10.000 Euro Kapital, 0,5 Prozent Risiko und einem Stop 10 Prozent unter dem
Einstieg ergibt das 50 Euro Risiko und eine Position von 500 Euro. Liegt der
Stop nur 2 Prozent entfernt, wird dieselbe Risikosumme zu einer Position von
2.500 Euro. Das ist der Punkt: die Position richtet sich nach dem Abstand, nicht
nach einem festen Prozentsatz des Depots.

Der Ueberzeugungsfaktor liegt zwischen 0,25 und 2,0 und kommt aus der Note der
Person und der Groesse des Trades im Verhaeltnis zu ihren sonstigen.

## Woher der Stop kommt

1. Aus der Quelle, wenn sie einen liefert. Trader-Plattformen tun das.
2. Sonst aus der Schwankungsbreite: 2,5 mal ATR unter dem Einstieg.
3. Wenn auch das nicht geht: 8 Prozent Notfall-Stop. Bei Hebelprodukten wird
   der Trade stattdessen abgelehnt, weil dort ein geschaetzter Stop nicht
   reicht.

Das Ziel liegt standardmaessig beim Dreifachen des Risikos.

## Deckel

| Grenze | Voreinstellung | Wogegen |
|---|---|---|
| `risk_per_trade_pct` | 0,5 | ein einzelner Fehlgriff |
| `max_position_pct` | 5 | ein enger Stop, der zu einer Riesenposition fuehrt |
| `max_ticker_exposure_pct` | 8 | Haeufung ueber mehrere Vorbilder hinweg |
| `max_gross_exposure_pct` | 80 | Vollinvestition ohne Reserve |
| `max_open_positions` | 20 | Unuebersichtlichkeit |
| `daily_loss_limit_pct` | 3 | ein schlechter Tag |
| `max_drawdown_pct` | 12 | eine schlechte Phase |

Der Deckel pro Titel ist der, den die meisten vergessen. Wenn fuenf
freigegebene Personen innerhalb einer Woche denselben Wert kaufen, entsteht
ohne ihn still und leise eine Position in fuenffacher Groesse, und zwar genau
dann, wenn alle dasselbe denken.

## Notaus

Reisst der Tagesverlust oder der Gesamtrueckgang seine Grenze, schaltet der Bot
ab. Er eroeffnet nichts Neues mehr, bestehende Stops bleiben aktiv, und die
Sperre bleibt bestehen, bis jemand sie bewusst aufhebt.

```bash
coattail kill "Grund"     # von Hand
coattail resume           # wieder freigeben
```

Bewusst kein Automatismus, der nach einer Weile von selbst zuruecksetzt. Wenn
eine Grenze gerissen ist, soll ein Mensch nachsehen.

## Weitere Sperren

Ein Signal wird auch abgelehnt, wenn es aelter ist als
`max_signal_age_minutes`, wenn der Titel auf der Sperrliste steht, wenn eine
Positivliste gesetzt ist und er nicht darauf steht, wenn es ein Leerverkauf
waere und die sind ausgeschaltet, wenn es eine Option waere und die sind
ausgeschaltet, oder wenn die Ordergroesse unter `min_order_notional` faellt.

Jede Ablehnung steht mit Grund in `coattail signals --status rejected`.

## Einstellungen, die man wirklich anfassen sollte

`equity_base` auf den tatsaechlichen Betrag. Im Papierhandel ist das der
Startwert der Simulation, im Echtbetrieb wird der Kontostand beim Broker
abgefragt.

`risk_per_trade_pct` fuer den Anfang halbieren. 0,25 statt 0,5.

`daily_loss_limit_pct` und `max_drawdown_pct` auf Werte, die man aushaelt. Die
Voreinstellungen sind ein Vorschlag, kein Ergebnis.

`trading_hours_only` auf `true` lassen. Orders ausserhalb der Handelszeiten
laufen in duenne Buecher.
