# Rechtliches und Fairness

Keine Rechtsberatung. Was hier steht, ist der Stand, auf dem das Projekt
gebaut ist, und die Punkte, die man selbst pruefen sollte.

## Kongress-Meldungen kopieren

Der STOCK Act von 2012 verpflichtet Mitglieder des US-Kongresses, Geschaefte
ueber 1.000 Dollar innerhalb von 45 Tagen zu melden. Diese Meldungen sind
oeffentlich. Sie zu lesen, auszuwerten und danach zu handeln ist in Ordnung.
Genau dafuer wurde das Gesetz gemacht.

Der Unterschied zum Insiderhandel liegt in der Oeffentlichkeit der Information.
Wer eine veroeffentlichte Meldung auswertet, handelt auf oeffentlicher
Grundlage. Wer nicht veroeffentlichte Kenntnisse verwendet, egal woher, tut es
nicht. Der Bot fasst ausschliesslich veroeffentlichte Daten an.

Dasselbe gilt fuer Form 4 und 13F bei der SEC.

## Umgang mit fremden Servern

Die Quellen unterscheiden sich darin, wie ausdruecklich sie den automatischen
Abruf erlauben.

**Ausdruecklich vorgesehen:** SEC EDGAR, Federal Register, USASpending,
Congress.gov, Bluesky, Hyperliquid. Alle betreiben eine dokumentierte
Schnittstelle fuer genau diesen Zweck. Die SEC verlangt eine Kontaktadresse im
User-Agent und hoechstens zehn Anfragen pro Sekunde, beides haelt der Bot ein,
sofern `COATTAIL_CONTACT` gesetzt ist.

**Geduldet:** die S3-Buckets von House und Senate Stock Watcher und das
JSON-Backend von Capitol Trades. Offen erreichbar, ohne ausdrueckliche
Erlaubnis. Der Bot fragt selten an und identifiziert sich.

**Vorher pruefen:** der `cookie`-Modus von Invo und alles, was ueber
`leaderboard` an eine Plattform gehaengt wird. Ein internes Backend mit der
eigenen Sitzung anzusprechen kann gegen die Nutzungsbedingungen verstossen,
auch wenn es technisch funktioniert. Deshalb ist `chain` der voreingestellte
Modus: Hyperliquid-Daten liegen ohnehin offen auf der Kette, dort stellt sich
die Frage nicht.

Der gemeinsame HTTP-Client hat eine Mindestpause zwischen Anfragen pro Host und
wiederholt mit wachsendem Abstand statt sofort. Wer die Intervalle in der
Konfiguration heruntersetzt, umgeht das nicht, aber er strapaziert die Geduld
der Betreiber.

## Wertpapierhandel

Automatisierter Handel fuer das eigene Konto ist in Deutschland und den meisten
anderen Laendern erlaubt. Wer fremdes Geld verwaltet oder Signale gegen
Entgelt weitergibt, bewegt sich in Richtung Erlaubnispflicht. Das ist eine
andere Liga und nichts, was man nebenbei klaert.

Gewinne aus Wertpapiergeschaeften sind steuerpflichtig. Bei einem auslaendischen
Broker ohne Abgeltungsteuer ist die Erklaerung Sache des Anlegers. Die
Datenbank enthaelt jeden Auftrag mit Zeitstempel, das hilft.

## Was der Bot nicht tut

Er handelt nicht auf nicht veroeffentlichten Informationen. Er umgeht keine
Zugangsbeschraenkung, keinen Bot-Schutz und kein Captcha. Er gibt sich nicht
als Browser oder als jemand anderes aus. Er hat keine Funktion zur
Marktmanipulation, kein Layering, kein Spoofing, keine abgestimmten Orders.

Die Idee dahinter: eine oeffentliche Meldung auszuwerten ist Fleissarbeit, die
jeder machen koennte. Genau deshalb ist sie in Ordnung.

## Haftung

MIT-Lizenz. Keine Anlageberatung, keine Gewaehr, keine Haftung. Handel mit
Wertpapieren und erst recht mit gehebelten Produkten kann zum Totalverlust
fuehren. Ein Bot, der ohne Aufsicht laeuft, erreicht diesen Zustand schneller
als ein Mensch.
