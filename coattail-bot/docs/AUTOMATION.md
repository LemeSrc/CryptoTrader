# Vom Laptop zum Dauerbetrieb

Der Plan geht von null bis zu einem Bot, der ohne Zutun im Hintergrund laeuft.
Die Zeitangaben sind Mindestwerte, keine Ziele. Wer Phase 4 und 6 abkuerzt,
spart ein paar Wochen und zahlt sie mit Geld zurueck.

**Reihenfolge nicht vertauschen.** Jede Phase prueft etwas, das die naechste
voraussetzt.

---

## Phase 0: Voraussetzungen

Was gebraucht wird:

- Python 3.11 oder neuer
- Ein Server, der durchlaeuft. Der kleinste VPS reicht, ein Raspberry Pi auch.
  Ein Laptop, der zuklappt, reicht nicht.
- Fuer US-Aktien ein Alpaca-Konto. Das Papierkonto ist kostenlos und laeuft
  unter derselben Schnittstelle wie das echte, also ist der Wechsel spaeter
  eine Zeile.
- Optional ein Telegram-Bot fuer Benachrichtigungen. Ueber @BotFather in zwei
  Minuten erstellt.

Was ausdruecklich nicht gebraucht wird: ein bezahltes Datenabo. Die kostenlosen
Quellen decken Kongress, Insider und Wallet-Fills vollstaendig ab. QuiverQuant
kauft Minuten, nicht Daten.

---

## Phase 1: Lokal einrichten (ein Abend)

```bash
git clone <dein-repo> coattail && cd coattail
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[brokers,llm]"

cp .env.example .env
$EDITOR .env          # COATTAIL_CONTACT ausfuellen, Rest kann leer bleiben
coattail doctor
```

`COATTAIL_CONTACT` ist kein Formalismus. Die SEC verlangt eine Kontaktadresse
im User-Agent und sperrt IPs, die ohne anfragen.

Seit Version 0.2 steht die Konfiguration schon auf echten Daten und
Papierhandel. Ein Durchlauf mit Demodaten ist nur noch fuer Tests gedacht.

---

## Phase 2: Echte Quellen pruefen und Historie laden (ein Abend)

Eingeschaltet sind ab Werk: `house_ptr` und `senate_ptr` (amtliche
Meldungen beider Kammern ueber einen taeglichen Spiegel auf GitHub),
`sec_form4` (Insiderkaeufe), `rss` (Beitraege von Donald Trump ueber das
Archiv trumpstruth.org, Verfuegungen des Weissen Hauses), `bluesky` (einige
Abgeordnete), `federal_register` und `usaspending`. Alle kostenlos, alle
ohne Registrierung.

Aus, weil vom Rechenzentrum aus gesperrt: `capitoltrades` (Bot-Pruefung),
`truthsocial` direkt (Cloudflare) und die Senatsseite selbst (nur aus den
USA erreichbar, deshalb der Spiegel).

Zuerst schauen, was vom eigenen Server aus tatsaechlich ankommt:

```bash
coattail probe
```

Eine Quelle mit 0 Treffern ueber sieben Tage ist entweder gesperrt oder hat ihr
Format geaendert. Das Log (`journalctl -u coattail`) sagt, was davon.

Dann die Historie laden. Wer vorher Demodaten hatte, legt die alte Datenbank
beiseite, sonst stehen erfundene Personen in der Rangliste:

```bash
mv data/coattail.db data/coattail.db.demo
coattail bootstrap        # holt rund drei Jahre Kongressmeldungen, dauert
```

Alternativ einfach den Dienst starten. Findet er keine aktuelle Bewertung,
laedt er die Historie selbst und bewertet 20 Minuten nach dem Start.

`stockwatcher`, frueher die ergiebigste Gratisquelle, antwortet im September
2026 nur noch mit 403 und ist deshalb aus.

---

## Phase 3: Auswahl treffen (ein Abend, danach automatisch)

```bash
coattail score              # rechnet jede Person durch, dauert beim ersten Mal
coattail actors --eligible
coattail backtest --days 730
```

Die Bewertung laedt fuer jeden Ticker eine Kursreihe. Beim ersten Lauf sind das
ein paar hundert Abrufe, danach greift der Cache unter `data/prices/`.

Was man sich jetzt anschaut:

- Wie viele Personen bestehen? Bei den Voreinstellungen sind das
  erfahrungsgemaess wenige. Das ist Absicht. Wer dreissig Namen freigegeben
  sehen will, sollte nicht die Schwelle senken, sondern sich fragen, warum.
- Stimmt die Reihenfolge mit dem ueberein, was man aus der Presse kennt? Wenn
  eine bekannt erfolgreiche Person durchfaellt, lohnt ein Blick in
  `coattail why NAME`. Oft ist es die Meldeverzoegerung, und das ist dann eine
  richtige Ablehnung, keine falsche.
- Die Rueckrechnung ist optimistisch. Sie kennt die Auswahl bereits. Ein
  Vorsprung von unter einem Prozent pro Trade ist nach Kosten nichts.

Einzelne Personen von Hand steuern:

```bash
coattail set-override "Name" allow    # trotz Note kopieren
coattail set-override "Name" block    # nie kopieren
coattail set-override "Name" clear
```

Ab hier laeuft die Bewertung jede Nacht um 03:20 von selbst.

---

## Phase 4: Trockenlauf, mindestens eine Woche

```yaml
execution:
  mode: dry_run
```

```bash
coattail run
```

Der Bot rechnet jetzt alles durch und schickt nichts ab. Jeden Abend:

```bash
coattail signals --status rejected -n 40
coattail orders
```

Worauf man achtet:

- Werden ueberhaupt Signale erzeugt? Null Signale ueber eine Woche heisst, die
  Freigabeschwelle oder `max_signal_age_minutes` ist zu eng.
- Wie oft und warum wird abgelehnt? "Signal ist zu alt" bei fast allen
  Meldungen heisst, man kopiert die falsche Personengruppe.
- Sehen die Positionsgroessen plausibel aus? Wenn eine Order das Kapital
  sprengen wuerde, greift der Deckel, und die Anmerkung im Auftrag sagt es.

Wer hier nichts anpasst, hat vermutlich nicht hingesehen.

---

## Phase 5: Auf den Server (ein Nachmittag)

### Variante A: systemd, der Normalfall

```bash
scp -r coattail user@server:/opt/
ssh user@server
cd /opt/coattail
sudo bash deploy/setup_vps.sh        # Python, venv, Dienst, Logrotation
sudo systemctl enable --now coattail
sudo systemctl status coattail
journalctl -u coattail -f
```

Der Dienst startet nach einem Neustart von selbst und wird bei einem Absturz
nach zehn Sekunden neu gestartet. `misfire_grace_time` sorgt dafuer, dass nach
einem Neustart nicht alle verpassten Laeufe auf einmal nachgeholt werden.

### Updates einspielen

Aus dem geklonten Repo heraus, nicht aus `/opt/coattail`:

```bash
cd ~/CryptoTrader/coattail-bot
sudo bash deploy/update.sh               # holt, kopiert, installiert neu, prueft
sudo bash deploy/update.sh --reset-db    # dazu mit leerer Datenbank anfangen
sudo bash deploy/update.sh --keep-config # eigene config.yaml nicht ersetzen
```

Datenbank und Konfiguration werden dabei nie geloescht, nur mit Datum
umbenannt. Das Skript laesst den Dienst aus, damit man vorher noch
`coattail bootstrap` laufen lassen kann.

### Variante B: Docker

```bash
docker compose up -d
docker compose logs -f
```

Datenbank und Kurscache liegen in einem Volume und ueberleben ein `pull`.

### Variante C: GitHub Actions, ohne eigenen Server

Fuer Kongress-Meldungen reicht das, weil die ohnehin nur ein paar Mal am Tag
erscheinen. Fuer Posts reicht es nicht, weil der kuerzeste Takt bei Actions
fuenf Minuten ist und in der Praxis eher fuenfzehn.

Die fertige Datei liegt unter `.github/workflows/scheduled.yml`. Sie braucht
die Zugangsdaten als Repository-Secrets und legt die Datenbank als Artefakt ab,
damit der naechste Lauf darauf aufsetzt. Ein Dauerbetrieb ist das nicht, aber
ein brauchbarer Einstieg ohne laufende Kosten.

---

## Phase 6: Papierhandel, mindestens vier Wochen

Telegram einschalten, sonst merkt man nichts:

```yaml
notify:
  telegram_enabled: true
```

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Die Voreinstellung ist bereits Papierhandel mit dem eigenen Papierdepot.
Das rechnet mit echten Kursen, Schlupf und Gebuehren und fuellt Aktienorders
nur zu US-Handelszeiten. Wer die Ausfuehrung naeher an der Wirklichkeit sehen
will, nimmt das kostenlose Papierkonto von Alpaca:

```yaml
execution:
  mode: paper
  broker: alpaca      # oder paper fuer die eigene Simulation
  alpaca_paper: true
```

Vier Wochen sind kein willkuerlicher Wert. Kuerzer bekommt man nicht genug
Signale zusammen, um ueberhaupt etwas zu sehen, und man erlebt keinen einzigen
schlechten Tag.

Woran man erkennt, dass es laeuft:

- Der Tagesbericht kommt jeden Abend.
- Stops werden tatsaechlich ausgeloest. Wenn in vier Wochen kein einziger
  greift, sind sie zu weit.
- Der Notaus wurde nicht ausgeloest. Falls doch: nachsehen warum, bevor man
  ihn zuruecksetzt.

---

## Phase 7: Echtgeld, klein

```yaml
execution:
  mode: live
  broker: alpaca
  alpaca_paper: false
risk:
  equity_base: 1000          # anfangen mit dem, was wehtun darf
  risk_per_trade_pct: 0.25   # halbe Voreinstellung fuer den Anfang
```

Checkliste vor dem Umlegen:

- [ ] Vier Wochen Papierhandel ohne Eingriff ueberstanden
- [ ] Notaus mindestens einmal von Hand getestet (`coattail kill`, dann `resume`)
- [ ] Benachrichtigungen kommen zuverlaessig an
- [ ] `max_drawdown_pct` und `daily_loss_limit_pct` auf Werte gesetzt, die man
      tatsaechlich aushaelt, nicht auf die Voreinstellung
- [ ] Datenbank wird gesichert (`deploy/backup.sh` in die Crontab)
- [ ] Verstanden, welche Personen kopiert werden und warum

Die ersten vier Wochen echt laufen lassen und nichts anfassen. Wer nach drei
Tagen an den Schwellen dreht, misst nur noch sich selbst.

---

## Betrieb

### Takte

| Aufgabe | Intervall | Warum |
|---|---|---|
| Posts holen | 60 Sekunden | der Vorsprung ist der einzige Grund dafuer |
| Meldungen holen | 15 Minuten | erscheinen in Schueben, schneller bringt nichts |
| Signale bilden | 1 Minute | direkt nach dem Eintreffen |
| Auftraege | 1 Minute | |
| Abgleich, Stops | 5 Minuten | Stops sollen nicht bis zum naechsten Abruf warten |
| Neubewertung | taeglich 03:20 | zieht viele Kursreihen |
| Tagesbericht | taeglich 21:05 UTC | |

### Was regelmaessig anzusehen ist

Taeglich reicht der Tagesbericht auf dem Telefon. Woechentlich:

```bash
coattail actors --eligible          # hat sich die Auswahl verschoben
coattail signals --status rejected  # neue Ablehnungsgruende
journalctl -u coattail --since "7 days ago" | grep -iE "fehler|error|warn"
```

Monatlich einmal `coattail backtest --days 365` gegen die tatsaechlichen
Ergebnisse halten. Wenn die Rueckrechnung deutlich besser aussieht als die
Wirklichkeit, ist der Unterschied der Schlupf, und dann gehoeren die duennen
Titel auf die Sperrliste.

### Wenn etwas klemmt

| Symptom | Ursache meistens | Abhilfe |
|---|---|---|
| keine Signale | niemand freigegeben | `coattail actors`, Schwellen ansehen |
| alle abgelehnt "zu alt" | Quelle meldet mit Verzug | `sec_form4` dazunehmen |
| "kein Kurs fuer X" | Ticker existiert nicht mehr | auf die Sperrliste |
| Notaus ausgeloest | Grenze gerissen | Grund pruefen, dann `coattail resume` |
| Quelle liefert nichts | Format geaendert | `coattail ingest --source X`, Log lesen |
| Dienst startet nicht | Pfad oder Rechte | `journalctl -u coattail -n 50` |

### Sicherung

Die Datenbank enthaelt die gesamte Historie und alle Bewertungen. Der
Kurscache ist ersetzbar.

```bash
0 4 * * * /opt/coattail/deploy/backup.sh
```

---

## Kosten

| Posten | Kosten |
|---|---|
| VPS | 4 bis 6 Euro im Monat |
| Kongress- und Insiderdaten | 0 |
| Kursdaten | 0 |
| Alpaca | 0 Provision bei US-Aktien |
| Telegram | 0 |
| KI-Auswertung der Posts | nur bei Bedarf, je nach Modell und Menge |
| QuiverQuant | dreistellig im Jahr, optional |

Ohne die letzten beiden Punkte laeuft der Bot fuer den Preis des Servers.
