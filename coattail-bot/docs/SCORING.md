# Wie aus einer Historie eine Note wird

Die Frage ist nicht "hat diese Person Gewinne gemacht", sondern "haette ich
Gewinn gemacht, wenn ich sie kopiert haette". Das ist etwas anderes, und der
Unterschied heisst Meldeverzoegerung.

## Zwei Verfahren

**Politiker und Insider** melden nur, dass sie gekauft oder verkauft haben. Es
gibt keinen Ausstieg, keinen Stop, keine Groesse ausser einer Spanne. Ihre
Qualitaet muss aus dem Kursverlauf nach dem Ereignis rekonstruiert werden.

Fuer jeden Trade wird die Rendite ueber 21, 63 und 126 Tage berechnet, die
Rendite des Vergleichsindex abgezogen und bei Verkaeufen das Vorzeichen
gedreht. Uebrig bleibt der Vorsprung gegenueber dem Markt.

**Trader auf Ausfuehrungsplattformen** liefern echte Fills. Dort werden
Rundlaeufe nach dem FIFO-Verfahren gebildet, und Trefferquote, Ergebnis,
Haltedauer, Risiko pro Trade und Stopdisziplin lassen sich direkt rechnen.
Wo ein Stop bekannt ist, zusaetzlich in R, also in Vielfachen des
eingegangenen Risikos. Das ist die ehrlichste Zahl im Copy-Trading, weil sie
zeigt, ob jemand seine Verluste kurz haelt.

## Ab Handelstag oder ab Meldetag

Beide Werte werden getrennt gespeichert:

```
alpha_from_trade        Rendite ab dem Tag des Geschaefts
alpha_from_disclosure   Rendite ab dem Tag der Veroeffentlichung
```

Nur der zweite Wert geht in die Note ein. Der erste steht daneben, weil die
Luecke zwischen beiden interessant ist: ist sie gross, war der Trade gut, aber
die Bewegung ist bis zur Meldung gelaufen. Die Person ist dann vielleicht eine
gute Traderin und trotzdem als Vorbild wertlos.

`coattail why NAME` zeigt beide Werte nebeneinander.

## Trefferquote, aber geschrumpft

Sieben von zehn Trades gewonnen sind keine 70 Prozent. Es sind zu wenige Daten.

```
geschrumpft = (Gewinne + Prior * Gewicht) / (Trades + Gewicht)
```

Mit Prior 0.5 und Gewicht 20 wird aus 7/10 der Wert 0.57, aus 70/100 dagegen
0.67. Wer genug Trades hat, behaelt seine Quote. Wer wenige hat, landet nahe am
Muenzwurf. Das verhindert genau den Fehler, an dem Ranglisten scheitern: oben
steht der mit vier Treffern in Folge.

Daneben steht in `detail` die untere Grenze des Wilson-Intervalls, also die
pessimistische Lesart. Das ist die Zahl, auf die man Geld setzen sollte.

## Die Note

Sechs Bestandteile, jeder auf 0 bis 1 normiert, dann gewichtet:

| Bestandteil | Gewicht | Was er misst |
|---|---|---|
| Trefferquote | 0.30 | geschrumpft, nicht roh |
| Erwartung | 0.25 | mittlerer Vorsprung pro Trade |
| Gewinnfaktor | 0.15 | Summe der Gewinne durch Summe der Verluste |
| Bestaendigkeit | 0.10 | Anteil der Jahre mit positivem Ergebnis |
| Risikodisziplin | 0.10 | Verhaeltnis Gewinn zu Verlust, Stops, Rueckgang |
| Aktualitaet | 0.10 | halbiert sich jaehrlich |

Davon abgezogen wird ein Abschlag fuer den groessten Rueckgang.

Bestaendigkeit ist der Bestandteil, der am meisten trennt. Drei brauchbare
Jahre sind etwas anderes als ein Volltreffer und zwei magere Jahre, auch wenn
die Summe gleich aussieht.

## Ausschlusskriterien

Die Note entscheidet nichts. Sie sortiert nur. Ueber das Kopieren entscheiden
harte Kriterien, und ein hoher Punktwert hebt keines davon auf:

| Kriterium | Voreinstellung | Warum |
|---|---|---|
| auswertbare Trades | mindestens 12 | weniger ist Zufall |
| geschrumpfte Trefferquote | mindestens 45 Prozent | |
| Note | mindestens 55 | |
| Erwartung | groesser null | |
| Meldeverzoegerung im Median | hoechstens 45 Tage | sonst nicht kopierbar |
| groesster Rueckgang | hoechstens 60 Prozent | |
| Aktualitaet | mindestens 0.15 | seit Jahren inaktiv |

Jede Ablehnung wird im Klartext gespeichert und ist in `coattail actors` und
`coattail why` sichtbar. Der Bot sagt immer, warum er jemanden nicht kopiert.

## Von Hand eingreifen

```bash
coattail set-override "Name" allow    # umgeht alle Kriterien
coattail set-override "Name" block    # sperrt unabhaengig von der Note
coattail set-override "Name" clear
```

`allow` ist mit Vorsicht zu geniessen. Es hebt auch die Pruefung auf die
Meldeverzoegerung auf, und das ist meistens genau die Pruefung, die gegriffen
hat.

## Was das Verfahren nicht kann

Es kennt nur Kaeufe und Verkaeufe, nicht die Absicht. Ein Verkauf zur
Finanzierung eines Hauskaufs sieht aus wie eine Meinung zum Unternehmen.

Es bewertet Personen, die heute in der Datenbank stehen. Wer ausgeschieden ist,
taucht nicht mehr auf. Der Durchschnitt der Verbliebenen sieht dadurch besser
aus, als er war.

Es nimmt Tagesschlusskurse. Innerhalb eines Tages passiert nichts, und
Dividenden sind nur ueber die bereinigten Kurse drin.

Die Ergebnisse einzelner Personen sind nicht unabhaengig. Wenn zwanzig
Abgeordnete im selben Monat denselben Technologiewert kaufen, ist das ein
Ereignis und nicht zwanzig.
