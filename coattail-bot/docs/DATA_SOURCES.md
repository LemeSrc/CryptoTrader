# Datenquellen

Jede Quelle steht in `config/config.yaml` und laesst sich einzeln an- und
abschalten. Die Ueberschneidung ist Absicht: doppelte Meldungen kosten ein paar
Requests, eine unbemerkt ausgefallene Quelle kostet Signale.

## Uebersicht

| Quelle | Was | Verzoegerung | Kosten | Schluessel |
|---|---|---|---|---|
| `stockwatcher` | Kongress, Vollhistorie | taeglich | 0 | nein |
| `capitoltrades` | Kongress, frisch | Minuten nach Einreichung | 0 | nein |
| `quiver` | Kongress, sehr frisch | Minuten | Abo | ja |
| `finnhub` | Kongress pro Ticker | Stunden | Freikontingent | ja |
| `fmp` | Kongress als Feed | Stunden | Freikontingent | ja |
| `sec_form4` | Insider eines Unternehmens | **zwei Arbeitstage** | 0 | nein |
| `sec_13f` | Fondspositionen | 45 Tage nach Quartal | 0 | nein |
| `hyperliquid` | Fills beliebiger Wallets | **Sekunden** | 0 | nein |
| `invo` | Social-Trading-Plattform | Sekunden | Konto | je nach Modus |
| `leaderboard` | beliebiges JSON-Ranking | je nach Anbieter | je nach Anbieter | je nach Anbieter |
| `bluesky` | Posts | Sekunden | 0 | nein |
| `mastodon` | Posts, Truth Social | Sekunden | 0 | meist nein |
| `x` | Posts | Sekunden | Abo | ja |
| `rss` | beliebige Feeds | Minuten | 0 | nein |
| `federal_register` | Praesidialverfuegungen | Stunden | 0 | nein |
| `usaspending` | Bundesauftraege | Tage | 0 | nein |
| `demo` | erfundene Daten | egal | 0 | nein |

---

## Kongress

### stockwatcher

Zwei oeffentliche S3-Buckets mit der aufbereiteten Vollhistorie von Haus und
Senat. Kein Key, keine Registrierung, keine Obergrenze. Das ist die Basis fuer
die Bewertung, weil man sofort Jahre an Historie hat statt auf sie zu warten.

Die Dateien sind gross. Ein Abruf alle sechs Stunden reicht vollkommen.

### capitoltrades

Das JSON-Backend der Website. Liefert die frischesten Eintraege am schnellsten
und kennt zusaetzlich Ausschusszugehoerigkeiten. Sortiert nach
Veroeffentlichungsdatum, deshalb kann der Abruf abbrechen, sobald er beim
letzten bekannten Stand angekommen ist.

### quiver, finnhub, fmp

Bezahlte oder teilbezahlte Anbieter. `quiver` ist der einzige, bei dem sich das
Geld in Minuten auszahlt. `finnhub` arbeitet pro Ticker und passt, wenn man
ohnehin eine feste Watchlist beobachtet. `fmp` ist der Ersatz, wenn die
kostenlosen Feeds ausfallen.

---

## Insider und Fonds

### sec_form4

Der schnellste frei verfuegbare Hinweis darauf, dass jemand mit
Informationsvorsprung gerade kauft. Kongress-Meldungen kommen mit bis zu 45
Tagen Verzug, Form 4 innerhalb von zwei Arbeitstagen.

Der Abruf geht ueber den Atom-Feed der aktuellen Einreichungen, dann pro
Einreichung ins eigentliche XML. Gefiltert wird auf Transaktionscode P und S,
also auf Kaeufe und Verkaeufe am offenen Markt. Ausuebungen von
Aktienoptionen und automatische Verkaufsplaene bleiben draussen, weil sie
nichts ueber eine Meinung aussagen.

Die SEC erwartet eine Kontaktadresse im User-Agent und hoechstens zehn
Anfragen pro Sekunde. `COATTAIL_CONTACT` setzen.

### sec_13f

Quartalsweise und mit 45 Tagen Verzug, also nichts zum schnellen Kopieren.
Taugt als Bestaetigung: wenn drei Fonds und zwei Abgeordnete denselben Titel
aufbauen, ist das etwas anderes als ein einzelner Kauf.

13F nennt CUSIP statt Ticker. Die Zuordnung ist noch nicht eingebaut, die
Eintraege landen mit leerem Symbol und werden nicht gehandelt.

---

## Trader kopieren

### hyperliquid

Der interessanteste Weg, und der am haeufigsten uebersehene.

Copy-Trading-Plattformen zeigen Ranglisten mit beeindruckenden Zahlen. Die
Zahlen stammen von der Plattform. Hyperliquid dagegen legt jeden Fill jeder
Wallet offen, ohne Konto und ohne Schluessel. Wer die Wallet einer Person
kennt, braucht die Plattform nicht mehr: Einstieg, Ausstieg, Groesse und
realisiertes Ergebnis stehen auf der Kette, und die Bewertung rechnet damit
echte Rundlaeufe statt behaupteter Trefferquoten.

```yaml
- name: hyperliquid
  enabled: true
  poll_seconds: 120
  options:
    wallets:
      - address: "0x...."
        label: "Trader aus dem Ranking"
```

Wie kommt man an die Wallet? Viele Trader verlinken sie selbst, in der Bio, im
Profil oder in einem Beitrag, weil die Nachpruefbarkeit ihr Verkaufsargument
ist. Der Weg ueber die Kette ist zudem der einzige, der keine
Nutzungsbedingungen beruehrt: die Daten sind oeffentlich.

### invo

Invo veroeffentlicht keine dokumentierte oeffentliche API. Deshalb drei Modi:

`chain` leitet auf Hyperliquid um und ist der empfohlene Weg.

`api` spricht einen offiziellen Endpunkt an, sobald es einen gibt. Basis-URL
und Pfade stehen in der Konfiguration, es muss kein Code angefasst werden.

`cookie` spricht das interne Backend mit der eigenen Sitzung an. Funktioniert,
solange man das eigene Konto benutzt. Ob die Nutzungsbedingungen das erlauben,
ist vorher zu pruefen, siehe [LEGAL.md](LEGAL.md).

### leaderboard

Bausatz fuer jede Plattform mit JSON-Rangliste. Statt fuer jede Seite eine
eigene Klasse beschreibt die Konfiguration, wo die Felder liegen:

```yaml
options:
  url: "https://api.beispiel.com/v1/traders/123/trades"
  root: data
  map:
    symbol: symbol
    side: side
    time: executedAt
    actor_id: trader.id
    actor_name: trader.displayName
    stop_loss: riskManagement.stopLoss
```

Punkte im Pfad gehen in verschachtelte Objekte. Damit haengt man ein
Binance-Copy-Portfolio oder einen eigenen Discord-Export an, ohne den Bot
anzufassen.

---

## Posts

### bluesky

Die einzige grosse Plattform mit einer offenen, kostenlosen und stabilen
Leseschnittstelle. Kein Token, kein Konto. Wenn eine beobachtete Person dort
ist, ist das der beste Weg.

### mastodon

Truth Social spricht die Mastodon-API. Ob der Abruf durchgeht, haengt am
Bot-Schutz davor. Wenn nicht: RSS-Spiegel als `rss`-Quelle eintragen.

### x

API v2, braucht ein bezahltes Kontingent. Nur sinnvoll, wenn die Person
nirgendwo sonst postet.

### rss

Unterschaetzt. Deckt Pressemitteilungen des Weissen Hauses, Ausschussmeldungen,
Nitter-Spiegel einzelner Konten und Substacks von Tradern ab. Ein Eintrag in
der Konfiguration, keine Zeile Code.

---

## Amtliche Vorgaenge

### federal_register

Jede Praesidialverfuegung erscheint dort, oft bevor die Nachrichtenlage sie
einordnet. Zoelle, Exportkontrollen, Energiegenehmigungen. Kein Key noetig.

### usaspending

Jeder vergebene Bundesauftrag mit Betrag und Empfaenger. Ein Auftrag ueber zwei
Milliarden an einen Ruestungskonzern ist ein harter Fakt und steht dort frueher
als in jeder Quartalsmitteilung.

Beide laufen als Textquelle durch denselben Klassifizierer wie Posts. Das
Ergebnis ist bewusst nur ein Zusatzsignal.

---

## Eigene Quelle bauen

Eine Klasse, zwei Methoden, ein Eintrag in der Registrierung:

```python
from coattail.sources.base import DisclosureSource, RawTrade

class MeineQuelle(DisclosureSource):
    name = "meine_quelle"
    requires = ("mein_api_key",)          # Feldname aus Secrets

    def fetch(self, since=None):
        for row in hole_daten():
            yield RawTrade(
                source=self.name,
                external_actor_id=row["id"],
                actor_name=row["name"],
                symbol=row["ticker"],
                side="buy",
                transaction_date=row["datum"],
                disclosed_at=row["gemeldet_am"],
            )
```

Danach in `sources/registry.py` eintragen und in der Konfiguration
einschalten. Deduplizierung, Cursor, Bewertung und Risikopruefung greifen
automatisch.
