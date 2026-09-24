# Coattail

Ein Bot, der Trades von Leuten kopiert, die nachweislich besser sind als der
Durchschnitt. Kongressabgeordnete, Unternehmensinsider und verifizierte Trader
auf Copy-Trading-Plattformen. Vorher wird jede Person durchgerechnet, danach
wird stur gespiegelt.

"Coattail investing" ist der etablierte Begriff dafuer: im Windschatten von
jemandem fahren, der mehr weiss.

> Der Bot startet im Trockenlauf mit Demodaten. Er braucht keinen einzigen
> Zugangsschluessel, um zu laufen, und er schickt keine Order ab, bevor man
> ihn zweimal bewusst umgestellt hat.

## Der Kerngedanke

Die meisten Copy-Trading-Bots scheitern an derselben Stelle: sie kopieren
jeden, der in der Liste steht. Aber ein Senat ist keine Ansammlung von
Ausnahmetalenten. Ein Teil der Abgeordneten schlaegt den Markt deutlich, die
Mehrheit liegt darunter, und wer alle zusammen kopiert, bekommt den
Durchschnitt minus Gebuehren.

Coattail dreht das um. Jede Person wird zuerst aus ihrer eigenen Historie
bewertet, und erst wer die Pruefung besteht, wird ueberhaupt kopiert.

Die wichtigste Zahl dabei ist nicht die Trefferquote, sondern die
Meldeverzoegerung. Eine Abgeordnete kann brillant handeln und fuer einen
Nachahmer trotzdem wertlos sein, weil ihre Meldung erst 44 Tage spaeter
erscheint und die Bewegung dann gelaufen ist. Deshalb rechnet Coattail beide
Werte getrennt: die Rendite ab Handelstag und die Rendite ab Meldetag. Nur die
zweite zaehlt fuer die Freigabe, denn frueher konnte niemand einsteigen.

```
Quellen  ->  Meldungen + Posts  ->  Bewertung pro Person  ->  Filter
                                                               |
                                                    nur Freigegebene
                                                               |
                                              Signal  ->  Risikopruefung
                                                               |
                                                   Groesse aus Stopabstand
                                                               |
                                                    Order  ->  Ueberwachung
```

## Was daran anders ist

**Kleine Stichproben werden bestraft.** Sieben von zehn Trades gewonnen heisst
nicht 70 Prozent Trefferquote, sondern zu wenig Daten. Die Bewertung zieht
solche Werte in Richtung Muenzwurf, bis genug Trades zusammenkommen.

**Gemessen wird gegen den Index.** Wer 2023 Technologiewerte kaufte, lag im
Plus. Das war der Markt, nicht das Koennen. Interessant ist nur, was ueber dem
Vergleichsindex uebrig bleibt.

**Kopiert wird die Richtung, nicht der Betrag.** Wer einem Senator mit
achtstelligem Depot in der Positionsgroesse eins zu eins folgt, ist nach einem
schlechten Monat fertig. Die Groesse kommt immer aus dem eigenen Kapital und
dem Abstand zum Stop.

**Mehrere Quellen pro Sache.** Kongress-Meldungen kommen aus bis zu fuenf
Feeds gleichzeitig. Faellt einer aus, merkt man es nicht. Doppelte werden ueber
einen Fingerprint aus Person, Ticker, Richtung und Handelsdatum verworfen.

**Nicht nur Pflichtmeldungen.** Form 4 der SEC erscheint binnen zwei
Arbeitstagen statt 45. Hyperliquid legt jeden Fill jeder Wallet offen, damit
laesst sich ein behaupteter Track Record nachrechnen statt ihm zu glauben. Und
das Federal Register veroeffentlicht Praesidialverfuegungen oft, bevor die
Nachrichtenlage sie einordnet.

## Schnellstart

```bash
git clone <dein-repo> coattail && cd coattail
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env          # COATTAIL_CONTACT eintragen, der Rest ist optional
coattail doctor               # zeigt, was bereit ist und was fehlt
coattail probe                # fragt jede Quelle einmal ab, ohne zu speichern
coattail bootstrap            # echte Historie laden und alle Personen bewerten
coattail actors --eligible    # wer die Pruefung besteht
coattail run                  # Dauerbetrieb mit Papierdepot
```

Die Voreinstellung ist Papierhandel mit echten Daten: echte Meldungen, echte
Posts, echte Kurse, aber ein simuliertes Depot mit Schlupf und Gebuehren.
Kostenlos und ohne Registrierung laufen `capitoltrades`, `sec_form4`,
`federal_register`, `usaspending`, `bluesky`, `truthsocial`, `rss` und
`hyperliquid`. X kostet pro gelesenem Post und ist deshalb erst einmal aus.

Fuer einen Blick ohne Netz gibt es weiter die Demoquelle. Dazu in der
Konfiguration `demo` ein- und alle anderen Quellen ausschalten, eine eigene
Datenbank nehmen und synthetische Kurse erzwingen:

```bash
COATTAIL_SYNTHETIC_PRICES=1 coattail bootstrap
```

## Befehle

| Befehl | Zweck |
|---|---|
| `coattail doctor` | Konfiguration, Zugangsdaten, Quellen, Kurse, Datenbank |
| `coattail probe [--source X]` | jede Quelle einmal abfragen, nichts speichern |
| `coattail bootstrap` | Erstbefuellung: Historie holen und alle bewerten |
| `coattail ingest [--source X]` | neue Daten holen |
| `coattail score` | Personen neu bewerten |
| `coattail actors [--eligible]` | Rangliste mit Note und Ablehnungsgrund |
| `coattail why NAME` | vollstaendige Begruendung fuer eine Person |
| `coattail set-override NAME allow\|block\|clear` | Person von Hand freigeben oder sperren |
| `coattail signals [--status rejected]` | Signale mit Status und Grund |
| `coattail positions` / `coattail orders` | offene Positionen, Auftragsbuch |
| `coattail run-once` | ein kompletter Durchlauf |
| `coattail run` | Dauerbetrieb mit allen Intervallen |
| `coattail backtest --days 730` | Rueckrechnung ab Meldetag, mit Kosten |
| `coattail kill` / `coattail resume` | Notaus und Freigabe |
| `coattail report` | Tagesbericht sofort |

## Dokumentation

| Datei | Inhalt |
|---|---|
| [docs/AUTOMATION.md](docs/AUTOMATION.md) | Schritt fuer Schritt vom Laptop zum Dauerbetrieb |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | jede Quelle mit Kosten, Verzoegerung und Einrichtung |
| [docs/SCORING.md](docs/SCORING.md) | wie aus einer Historie eine Note wird |
| [docs/RISK.md](docs/RISK.md) | Positionsgroesse, Deckel, Notaus |
| [docs/LEGAL.md](docs/LEGAL.md) | was erlaubt ist und was nicht |

## Betriebsmodi

`dry_run` rechnet alles durch und bricht in der letzten Zeile ab. Was im Log
steht, waere abgeschickt worden. Eine Woche Trockenlauf sagt mehr ueber die
eigenen Einstellungen als jede Rueckrechnung.

`paper` fuehrt gegen die eigene Datenbank aus, mit Schlupf und Gebuehren, oder
gegen das Papierkonto von Alpaca.

`live` handelt echt. Dafuer muss man `mode: live` setzen **und** einen Broker
mit Zugangsdaten hinterlegen. Beides zusammen, sonst faellt der Bot auf den
Papierhandel zurueck und sagt es im Log.

## Grenzen, die man kennen sollte

Die Rueckrechnung leidet an Ueberlebensverzerrung: bewertet werden Personen,
die heute in der Datenbank stehen, mit Kennzahlen aus derselben Historie. Die
Zahl taugt zum Vergleich von Einstellungen, nicht als Renditeversprechen.

Die Meldeverzoegerung im Kongress liegt im Median bei ueber dreissig Tagen.
Wer auf Tempo setzt, ist bei Form 4 und den Wallet-Fills besser aufgehoben.

Die Post-Auswertung ist der unsicherste Teil. Sie steht deshalb auf `false`,
und wenn sie an ist, gilt eine hoehere Schwelle fuer die Ueberzeugung.

Kopiertes Handeln verliert am Schlupf. Bei einem duenn gehandelten Titel kann
die eigene Order den Kurs bewegen, den man kopieren wollte. Dafuer gibt es die
Sperrliste.

## Lizenz

MIT. Keine Anlageberatung. Handel mit Wertpapieren kann zum Totalverlust
fuehren, und ein Bot, der ohne Aufsicht laeuft, macht das schneller als ein
Mensch.
