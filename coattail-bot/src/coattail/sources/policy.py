"""Amtliche Vorgaenge als Signalquelle.

Zwei Ideen, die in Copy-Trading-Projekten selten auftauchen, obwohl die Daten
offen und kostenlos sind:

Federal Register  Jede Praesidialverfuegung erscheint dort, oft Stunden bevor
                  die Nachrichtenlage sie einordnet. Zoelle, Exportkontrollen,
                  Energiegenehmigungen. Die API braucht keinen Key.

USASpending       Jeder vergebene Bundesauftrag mit Betrag und Empfaenger. Ein
                  Zwei-Milliarden-Auftrag an einen Ruestungskonzern ist ein
                  harter Fakt, kein Geruecht, und er steht dort frueher als in
                  jeder Quartalsmitteilung.

Beide liefern Text, der durch denselben Klassifizierer laeuft wie Posts. Das
Ergebnis ist bewusst nur ein Zusatzsignal: es erhoeht oder senkt die
Ueberzeugung bei einem bestehenden Kopiersignal, statt allein zu handeln.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable

from ..http import HttpClient
from ..util import to_utc
from .base import PostSource, RawPost

log = logging.getLogger(__name__)


class FederalRegisterSource(PostSource):
    name = "federal_register"
    BASE = "https://www.federalregister.gov/api/v1/documents.json"

    def available(self) -> tuple[bool, str]:
        return True, "bereit (kein Key noetig)"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawPost]:
        start = (since or dt.datetime.now(dt.UTC) - dt.timedelta(days=3)).date()
        params = {
            "per_page": self.options.get("per_page", 50),
            "order": "newest",
            "conditions[publication_date][gte]": start.isoformat(),
            "fields[]": [
                "title",
                "abstract",
                "document_number",
                "publication_date",
                "html_url",
                "presidential_document_type",
                "type",
                "agencies",
            ],
        }
        for doc_type in self.options.get("types", ["PRESDOCU", "RULE"]):
            params["conditions[type][]"] = doc_type
            with HttpClient() as client:
                try:
                    payload = client.json(self.BASE, params=params)
                except Exception as exc:  # noqa: BLE001
                    log.warning("federal register: %s", exc)
                    continue
            for row in payload.get("results", []):
                published = to_utc(row.get("publication_date"))
                if not published:
                    continue
                agencies = ", ".join(a.get("name", "") for a in row.get("agencies", []) or [])
                text = "\n".join(
                    part for part in [row.get("title"), row.get("abstract"), agencies] if part
                )
                yield RawPost(
                    platform=self.name,
                    external_id=row.get("document_number", ""),
                    author=row.get("presidential_document_type") or row.get("type") or "federal_register",
                    text=text,
                    posted_at=published,
                    url=row.get("html_url"),
                    raw={"type": doc_type},
                )


class UsaSpendingSource(PostSource):
    name = "usaspending"
    BASE = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

    def available(self) -> tuple[bool, str]:
        return True, "bereit (kein Key noetig)"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawPost]:
        start = (since or dt.datetime.now(dt.UTC) - dt.timedelta(days=7)).date()
        min_amount = float(self.options.get("min_amount", 100_000_000))
        body = {
            "filters": {
                "time_period": [{"start_date": start.isoformat(), "end_date": dt.date.today().isoformat()}],
                "award_type_codes": ["A", "B", "C", "D"],
                "award_amounts": [{"lower_bound": min_amount}],
            },
            "fields": [
                "Award ID",
                "Recipient Name",
                "Award Amount",
                "Awarding Agency",
                "Start Date",
                "Description",
            ],
            "sort": "Award Amount",
            "order": "desc",
            "limit": int(self.options.get("limit", 50)),
            "page": 1,
        }
        with HttpClient(headers={"Content-Type": "application/json"}) as client:
            try:
                resp = client.post(self.BASE, json=body)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("usaspending: %s", exc)
                return
        for row in payload.get("results", []):
            when = to_utc(row.get("Start Date")) or dt.datetime.now(dt.UTC)
            amount = row.get("Award Amount")
            recipient = row.get("Recipient Name", "")
            text = (
                f"Bundesauftrag ueber {amount} USD an {recipient}. "
                f"Vergebende Behoerde: {row.get('Awarding Agency', '')}. "
                f"{row.get('Description', '')}"
            )
            yield RawPost(
                platform=self.name,
                external_id=str(row.get("Award ID") or f"{recipient}:{amount}"),
                author=recipient,
                text=text,
                posted_at=when,
                url="https://www.usaspending.gov/search",
                raw={"amount": amount, "recipient": recipient},
            )
