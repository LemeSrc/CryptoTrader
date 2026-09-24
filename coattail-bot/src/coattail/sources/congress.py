"""Kongress-Disclosures aus mehreren Quellen.

Die Quellen ueberschneiden sich absichtlich. Wer nur eine anzapft, merkt nicht,
wenn sie stillsteht. Die Pipeline dedupliziert ueber einen Fingerprint aus
Person, Ticker, Richtung und Handelsdatum, deshalb kostet Redundanz nichts
ausser ein paar Requests.

Reihenfolge nach Nuetzlichkeit:
  stockwatcher   kostenlos, kein Key, taeglich aktualisierte Vollhistorie
  capitoltrades  kostenlos, schnell, JSON-Backend der Website
  quiver         bezahlt, sehr schnell, sauber strukturiert
  finnhub / fmp  Fallback mit kostenlosem Kontingent
"""

from __future__ import annotations

import datetime as dt
import logging
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
    """Das JSON-Backend von capitoltrades.com.

    Liefert die frischesten Eintraege am schnellsten und kennt zusaetzlich
    Ausschusszugehoerigkeiten, was fuer die Themenzuordnung nuetzlich ist.
    """

    name = "capitoltrades"
    BASE = "https://bff.capitoltrades.com/trades"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]:
        pages = int(self.options.get("pages", 3))
        page_size = int(self.options.get("page_size", 100))
        with HttpClient() as client:
            for page in range(1, pages + 1):
                params = {"page": page, "pageSize": page_size, "sortBy": "-pubDate"}
                try:
                    payload = client.json(self.BASE, params=params)
                except Exception as exc:  # noqa: BLE001
                    log.warning("capitoltrades Seite %d fehlgeschlagen: %s", page, exc)
                    return
                rows = payload.get("data") or []
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
        name = politician.get("fullName") or politician.get("name")
        symbol = clean_symbol((row.get("asset") or {}).get("assetTicker"))
        side = normalize_side(row.get("txType"))
        tx_date = to_date(row.get("txDate"))
        if not name or not side or not tx_date:
            return None
        return RawTrade(
            source=self.name,
            external_actor_id=str(politician.get("_politicianId") or name).lower(),
            actor_name=name,
            symbol=symbol,
            side=side,
            transaction_date=tx_date,
            disclosed_at=to_utc(row.get("pubDate") or row.get("filingDate")),
            amount_low=row.get("value"),
            amount_high=row.get("value"),
            price=row.get("price"),
            party=politician.get("party"),
            chamber=politician.get("chamber"),
            state=politician.get("_stateId"),
            asset_class=self._asset_class((row.get("asset") or {}).get("assetType")),
            raw=row,
        )

    @staticmethod
    def _asset_class(asset_type: str | None) -> str:
        t = (asset_type or "").lower()
        if "crypto" in t:
            return "crypto"
        if "option" in t:
            return "option"
        return "equity"


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
