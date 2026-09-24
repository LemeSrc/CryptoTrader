"""Kongress-Disclosures aus mehreren Quellen.

Die Quellen ueberschneiden sich absichtlich. Wer nur eine anzapft, merkt nicht,
wenn sie stillsteht. Die Pipeline dedupliziert ueber einen Fingerprint aus
Person, Ticker, Richtung und Handelsdatum, deshalb kostet Redundanz nichts
ausser ein paar Requests.

Reihenfolge nach Nuetzlichkeit:
  capitoltrades  kostenlos, schnell, JSON-Backend oder Tabellenseite
  quiver         bezahlt, sehr schnell, sauber strukturiert
  finnhub / fmp  Fallback mit kostenlosem Kontingent
  stockwatcher   frueher die beste Gratis-Historie, die Buckets antworten
                 im September 2026 mit 403. Bleibt drin, falls sie zurueckkommen
                 oder jemand einen Spiegel ueber house_url/senate_url eintraegt.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Iterable, Iterator

from ..http import HttpClient
from ..util import clean_symbol, parse_amount_range, to_date, to_utc
from .base import DisclosureSource, RawTrade, normalize_side

log = logging.getLogger(__name__)


class StockWatcherSource(DisclosureSource):
    """house-stock-watcher / senate-stock-watcher.

    Zwei oeffentliche S3-Buckets mit der kompletten aufbereiteten Historie der
    Pflichtmeldungen. Kein Key, keine Registrierung. Das ist die beste Basis
    fuer die Bewertung einer Person, weil man sofort Jahre an Historie hat.
    """

    name = "stockwatcher"

    HOUSE = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
    SENATE = (
        "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json"
    )

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]:
        urls = {"house": self.options.get("house_url", self.HOUSE)}
        if self.options.get("include_senate", True):
            urls["senate"] = self.options.get("senate_url", self.SENATE)
        with HttpClient(timeout=120.0) as client:
            for chamber, url in urls.items():
                try:
                    rows = client.json(url)
                except Exception as exc:  # noqa: BLE001
                    log.warning("stockwatcher %s nicht erreichbar: %s", chamber, exc)
                    continue
                log.info("stockwatcher %s: %d Zeilen", chamber, len(rows))
                yield from self._parse(rows, chamber, since)

    def _parse(
        self, rows: list[dict], chamber: str, since: dt.datetime | None
    ) -> Iterator[RawTrade]:
        for row in rows:
            name = (row.get("representative") or row.get("senator") or "").strip()
            symbol = clean_symbol(row.get("ticker"))
            side = normalize_side(row.get("type"))
            tx_date = to_date(row.get("transaction_date"))
            if not name or not side or not tx_date:
                continue
            disclosed = to_utc(row.get("disclosure_date"))
            if since and disclosed and disclosed < since:
                continue
            low, high = parse_amount_range(row.get("amount"))
            yield RawTrade(
                source=self.name,
                external_actor_id=name.lower().replace(" ", "_"),
                actor_name=name,
                actor_type="politician",
                symbol=symbol,
                asset_class="equity",
                side=side,
                transaction_date=tx_date,
                disclosed_at=disclosed,
                amount_low=low,
                amount_high=high,
                chamber=chamber,
                state=row.get("state"),
                party=row.get("party"),
                option_type="option" if "option" in str(row.get("asset_description", "")).lower() else None,
                raw=row,
            )


class CapitolTradesSource(DisclosureSource):
    """capitoltrades.com, kostenlos und ohne Schluessel.

    Zwei Wege, weil der Betreiber sein Backend nicht als Schnittstelle
    anbietet und es gelegentlich umbaut oder abschirmt. Zuerst das JSON-Backend
    der Website, das ist sauber strukturiert. Liefert es nichts Brauchbares,
    wird die oeffentliche Tabellenseite gelesen. Beide Wege liefern dieselben
    Felder, die Pipeline merkt keinen Unterschied.

    Ohne Cursor (erster Lauf, 'coattail bootstrap') werden history_pages
    Seiten geholt statt pages. Das ist die Historie fuer die Bewertung und
    dauert beim ersten Mal ein paar Minuten.
    """

    name = "capitoltrades"
    BASE = "https://bff.capitoltrades.com/trades"
    HTML = "https://www.capitoltrades.com/trades"
    HEADERS = {
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
        "Origin": "https://www.capitoltrades.com",
        "Referer": "https://www.capitoltrades.com/",
    }

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]:
        if since is None:
            pages = int(self.options.get("history_pages", 150))
        else:
            pages = int(self.options.get("pages", 3))
        page_size = int(self.options.get("page_size", 100))
        mode = self.options.get("mode", "auto")  # auto | json | html
        with HttpClient(headers=self.HEADERS) as client:
            if mode in ("auto", "json"):
                delivered = 0
                for trade in self._fetch_json(client, pages, page_size, since):
                    delivered += 1
                    yield trade
                if delivered or mode == "json":
                    return
                log.info("capitoltrades: JSON-Backend liefert nichts, lese die Tabellenseite")
            yield from self._fetch_html(client, pages, since)

    # ------------------------------------------------------------ JSON-Weg
    def _fetch_json(
        self, client: HttpClient, pages: int, page_size: int, since: dt.datetime | None
    ) -> Iterator[RawTrade]:
        for page in range(1, pages + 1):
            params = {"page": page, "pageSize": page_size, "sortBy": "-pubDate"}
            try:
                payload = client.json(self.BASE, params=params)
            except Exception as exc:  # noqa: BLE001
                log.warning("capitoltrades JSON Seite %d fehlgeschlagen: %s", page, exc)
                return
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not rows:
                return
            stop = False
            for row in rows:
                trade = self._parse_row(row)
                if trade is None:
                    continue
                if since and trade.disclosed_at and trade.disclosed_at < since:
                    stop = True
                    continue
                yield trade
            if stop:
                return

    def _parse_row(self, row: dict) -> RawTrade | None:
        politician = row.get("politician") or {}
        name = (
            politician.get("fullName")
            or politician.get("name")
            or f"{politician.get('firstName', '')} {politician.get('lastName', '')}".strip()
        )
        symbol = clean_symbol((row.get("asset") or {}).get("assetTicker"))
        side = normalize_side(row.get("txType"))
        tx_date = to_date(row.get("txDate"))
        if not name or not side or not tx_date:
            return None
        return RawTrade(
            source=self.name,
            external_actor_id=str(row.get("_politicianId") or politician.get("_politicianId") or name),
            actor_name=name,
            symbol=symbol,
            side=side,
            transaction_date=tx_date,
            disclosed_at=to_utc(row.get("pubDate") or row.get("filingDate")),
            amount_low=row.get("value"),
            amount_high=row.get("value"),
            price=row.get("price"),
            party=politician.get("party"),
            chamber=row.get("chamber") or politician.get("chamber"),
            state=politician.get("_stateId"),
            asset_class=self._asset_class((row.get("asset") or {}).get("assetType")),
            raw=row,
        )

    # ------------------------------------------------------------ HTML-Weg
    def _fetch_html(
        self, client: HttpClient, pages: int, since: dt.datetime | None
    ) -> Iterator[RawTrade]:
        for page in range(1, pages + 1):
            try:
                resp = client.get(self.HTML, params={"page": page, "pageSize": 96})
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                log.warning("capitoltrades Seite %d fehlgeschlagen: %s", page, exc)
                return
            trades = list(self.parse_html(resp.text))
            if not trades:
                if page == 1:
                    log.warning("capitoltrades: Tabellenseite ohne erkennbare Zeilen, Layout geaendert?")
                return
            stop = False
            for trade in trades:
                if since and trade.disclosed_at and trade.disclosed_at < since:
                    stop = True
                    continue
                yield trade
            if stop:
                return

    def parse_html(self, html: str, today: dt.date | None = None) -> Iterator[RawTrade]:
        from selectolax.parser import HTMLParser

        today = today or dt.datetime.now(dt.UTC).date()
        tree = HTMLParser(html)
        for row in tree.css("tbody tr"):
            cells = row.css("td")
            if len(cells) < 8:
                continue

            def pick(selector: str, _row=row) -> str:
                node = _row.css_first(selector)
                return node.text(strip=True) if node else ""

            name = pick(".politician-name")
            ticker = pick(".issuer-ticker")
            side = normalize_side(pick(".tx-type") or cells[6].text(strip=True))
            published = _ct_date(cells[2].text(separator=" ", strip=True), today)
            traded = _ct_date(cells[3].text(separator=" ", strip=True), today)
            if not name or not side or not traded:
                continue
            low, high = _ct_size(pick(".trade-size") or cells[7].text(strip=True))
            price = _num(cells[8].text(strip=True)) if len(cells) > 8 else None
            link = row.css_first(".politician-name a") or row.css_first("a[href*='/politicians/']")
            href = link.attributes.get("href", "") if link else ""
            yield RawTrade(
                source=self.name,
                external_actor_id=href.rsplit("/", 1)[-1] or name,
                actor_name=name,
                symbol=clean_symbol(ticker),
                side=side,
                transaction_date=traded,
                disclosed_at=(
                    dt.datetime.combine(published, dt.time(12, 0), tzinfo=dt.UTC)
                    if published
                    else None
                ),
                amount_low=low,
                amount_high=high,
                price=price,
                party=pick(".party") or None,
                chamber=pick(".chamber") or None,
                state=pick(".us-state-compact") or None,
                raw={"issuer": pick(".issuer-name"), "owner": cells[5].text(strip=True)},
            )

    @staticmethod
    def _asset_class(asset_type: str | None) -> str:
        t = (asset_type or "").lower()
        if "crypto" in t:
            return "crypto"
        if "option" in t:
            return "option"
        return "equity"


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
)}


def _ct_date(text: str, today: dt.date) -> dt.date | None:
    """Capitol Trades schreibt '23 Oct2025', '23 Oct 2025', 'Today' oder 'Yesterday'."""
    low = text.lower()
    if "today" in low:
        return today
    if "yesterday" in low:
        return today - dt.timedelta(days=1)
    m = re.search(r"(\d{1,2})\s*([A-Za-z]{3})[A-Za-z]*\s*(\d{4})", text)
    if not m:
        return None
    month = _MONTHS.get(m.group(2).lower())
    if not month:
        return None
    try:
        return dt.date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return None


def _ct_size(text: str) -> tuple[float | None, float | None]:
    """'1K–15K' wird zu (1000, 15000), '1M–5M' zu (1e6, 5e6)."""
    factors = {"k": 1e3, "m": 1e6, "b": 1e9}
    vals = [
        float(num.replace(",", "")) * factors.get(unit.lower(), 1.0)
        for num, unit in re.findall(r"([\d.,]+)\s*([KkMmBb]?)", text)
        if num.strip(".,")
    ]
    if not vals:
        return None, None
    return min(vals), max(vals)


class QuiverSource(DisclosureSource):
    """QuiverQuant. Kostenpflichtig, dafuer die kuerzeste Verzoegerung
    zwischen Einreichung und Verfuegbarkeit im Feed."""

    name = "quiver"
    requires = ("quiver_api_key",)
    BASE = "https://api.quiverquant.com/beta"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]:
        endpoint = self.options.get("endpoint", "/live/congresstrading")
        headers = {
            "Authorization": f"Token {self.secrets.quiver_api_key}",
            "Accept": "application/json",
        }
        with HttpClient(headers=headers) as client:
            try:
                rows = client.json(f"{self.BASE}{endpoint}")
            except Exception as exc:  # noqa: BLE001
                log.warning("quiver nicht erreichbar: %s", exc)
                return
        for row in rows:
            name = row.get("Representative") or row.get("Senator") or ""
            symbol = clean_symbol(row.get("Ticker"))
            side = normalize_side(row.get("Transaction"))
            tx_date = to_date(row.get("TransactionDate") or row.get("Date"))
            if not name or not side or not tx_date:
                continue
            disclosed = to_utc(row.get("ReportDate") or row.get("last_modified"))
            if since and disclosed and disclosed < since:
                continue
            low, high = parse_amount_range(row.get("Range") or row.get("Amount"))
            yield RawTrade(
                source=self.name,
                external_actor_id=name.lower().replace(" ", "_"),
                actor_name=name,
                symbol=symbol,
                side=side,
                transaction_date=tx_date,
                disclosed_at=disclosed,
                amount_low=low if low else _num(row.get("Amount")),
                amount_high=high if high else _num(row.get("Amount")),
                party=row.get("Party"),
                chamber=row.get("House") or row.get("Chamber"),
                raw=row,
            )


class FinnhubCongressSource(DisclosureSource):
    """Finnhub deckt Kongress-Trades pro Ticker ab. Nuetzlich, wenn man ohnehin
    eine Watchlist beobachtet und nur wissen will, wer dort gerade zugreift."""

    name = "finnhub"
    requires = ("finnhub_api_key",)
    BASE = "https://finnhub.io/api/v1/stock/congressional-trading"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]:
        symbols = [s.upper() for s in self.options.get("symbols", [])]
        if not symbols:
            log.info("finnhub: keine symbols konfiguriert, uebersprungen")
            return
        frm = (since or dt.datetime.now(dt.UTC) - dt.timedelta(days=90)).date().isoformat()
        to = dt.date.today().isoformat()
        with HttpClient() as client:
            for symbol in symbols:
                params = {
                    "symbol": symbol,
                    "from": frm,
                    "to": to,
                    "token": self.secrets.finnhub_api_key,
                }
                try:
                    payload = client.json(self.BASE, params=params)
                except Exception as exc:  # noqa: BLE001
                    log.warning("finnhub %s: %s", symbol, exc)
                    continue
                for row in payload.get("data", []):
                    side = normalize_side(row.get("transactionType"))
                    tx_date = to_date(row.get("transactionDate"))
                    name = row.get("name") or ""
                    if not side or not tx_date or not name:
                        continue
                    yield RawTrade(
                        source=self.name,
                        external_actor_id=name.lower().replace(" ", "_"),
                        actor_name=name,
                        symbol=clean_symbol(row.get("symbol") or symbol),
                        side=side,
                        transaction_date=tx_date,
                        disclosed_at=to_utc(row.get("filingDate")),
                        amount_low=_num(row.get("amountFrom")),
                        amount_high=_num(row.get("amountTo")),
                        chamber=row.get("ownerType"),
                        raw=row,
                    )


class FmpCongressSource(DisclosureSource):
    """Financial Modeling Prep, Senats- und Hausmeldungen als RSS-artiger Feed."""

    name = "fmp"
    requires = ("fmp_api_key",)
    ENDPOINTS = (
        "https://financialmodelingprep.com/api/v4/senate-trading-rss-feed",
        "https://financialmodelingprep.com/api/v4/senate-disclosure-rss-feed",
    )

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]:
        with HttpClient() as client:
            for endpoint in self.ENDPOINTS:
                for page in range(int(self.options.get("pages", 2))):
                    try:
                        rows = client.json(
                            endpoint, params={"page": page, "apikey": self.secrets.fmp_api_key}
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning("fmp %s: %s", endpoint, exc)
                        break
                    if not rows:
                        break
                    for row in rows:
                        name = (
                            f"{row.get('firstName', '')} {row.get('lastName', '')}".strip()
                            or row.get("representative")
                            or row.get("office")
                            or ""
                        )
                        side = normalize_side(row.get("type") or row.get("transactionType"))
                        tx_date = to_date(row.get("transactionDate") or row.get("dateRecieved"))
                        if not name or not side or not tx_date:
                            continue
                        low, high = parse_amount_range(row.get("amount"))
                        yield RawTrade(
                            source=self.name,
                            external_actor_id=name.lower().replace(" ", "_"),
                            actor_name=name,
                            symbol=clean_symbol(row.get("symbol") or row.get("ticker")),
                            side=side,
                            transaction_date=tx_date,
                            disclosed_at=to_utc(row.get("dateRecieved") or row.get("disclosureDate")),
                            amount_low=low,
                            amount_high=high,
                            chamber=row.get("office"),
                            raw=row,
                        )


def _num(value: object) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
