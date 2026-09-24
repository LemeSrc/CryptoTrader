"""SEC EDGAR: Form 4 (Insidergeschaefte) und 13F (Fondspositionen).

Warum das hier dazugehoert: Kongress-Meldungen kommen mit bis zu 45 Tagen
Verzoegerung, Form 4 dagegen innerhalb von zwei Arbeitstagen. Wenn ein CEO
eigene Aktien kauft, steht das praktisch in Echtzeit im Feed. Das ist die
schnellste frei verfuegbare Quelle fuer "jemand mit Informationsvorsprung
kauft gerade".

Die SEC verlangt eine Kontaktadresse im User-Agent und maximal 10 Requests
pro Sekunde. Beides regelt der gemeinsame HTTP-Client, COATTAIL_CONTACT
sollte trotzdem gesetzt sein.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable

from ..http import HttpClient
from ..util import clean_symbol, to_date, to_utc
from .base import DisclosureSource, RawTrade

log = logging.getLogger(__name__)

ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
ACC_RE = re.compile(r"(\d{10})-?(\d{2})-?(\d{6})")


class SecForm4Source(DisclosureSource):
    """Aktuelle Form-4-Meldungen von Unternehmensinsidern."""

    name = "sec_form4"
    CURRENT = "https://www.sec.gov/cgi-bin/browse-edgar"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]:
        count = int(self.options.get("count", 100))
        min_value = float(self.options.get("min_value_usd", 250_000))
        only_buys = bool(self.options.get("only_buys", True))
        params = {
            "action": "getcurrent",
            "type": "4",
            "owner": "include",
            "count": count,
            "output": "atom",
        }
        with HttpClient(headers={"Accept": "application/atom+xml"}) as client:
            try:
                resp = client.get(self.CURRENT, params=params)
                resp.raise_for_status()
                feed = ET.fromstring(resp.content)
            except Exception as exc:  # noqa: BLE001
                log.warning("SEC Atom-Feed nicht lesbar: %s", exc)
                return

            for entry in feed.findall("a:entry", ATOM_NS):
                link_el = entry.find("a:link", ATOM_NS)
                link = link_el.get("href") if link_el is not None else None
                updated = to_utc(entry.findtext("a:updated", default="", namespaces=ATOM_NS))
                if not link or (since and updated and updated < since):
                    continue
                try:
                    for trade in self._parse_filing(client, link, updated):
                        if only_buys and trade.side != "buy":
                            continue
                        value = (trade.price or 0) * (trade.quantity or 0)
                        if value and value < min_value:
                            continue
                        yield trade
                except Exception as exc:  # noqa: BLE001
                    log.debug("Form-4-Dokument uebersprungen (%s): %s", link, exc)

    def _parse_filing(
        self, client: HttpClient, index_url: str, updated: dt.datetime | None
    ) -> list[RawTrade]:
        """Vom Filing-Index zum eigentlichen ownershipDocument."""
        base = index_url.rsplit("/", 1)[0]
        listing = client.json(f"{base}/index.json")
        doc_name = None
        for item in listing.get("directory", {}).get("item", []):
            fname = item.get("name", "")
            if fname.endswith(".xml") and not fname.endswith("-index.xml"):
                doc_name = fname
                break
        if not doc_name:
            return []
        resp = client.get(f"{base}/{doc_name}")
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        issuer = root.find("issuer")
        symbol = clean_symbol(issuer.findtext("issuerTradingSymbol") if issuer is not None else None)
        owner = root.find("reportingOwner/reportingOwnerId")
        owner_name = (owner.findtext("rptOwnerName") if owner is not None else None) or "unbekannt"
        owner_cik = (owner.findtext("rptOwnerCik") if owner is not None else None) or owner_name
        relation = root.find("reportingOwner/reportingOwnerRelationship")
        role = _owner_role(relation)

        out: list[RawTrade] = []
        for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
            code = tx.findtext("transactionCoding/transactionCode")
            if code not in ("P", "S"):  # P = offener Kauf, S = offener Verkauf
                continue
            amounts = tx.find("transactionAmounts")
            if amounts is None:
                continue
            qty = _val(amounts, "transactionShares")
            price = _val(amounts, "transactionPricePerShare")
            tx_date = to_date(_text(tx, "transactionDate"))
            if not symbol or not tx_date:
                continue
            out.append(
                RawTrade(
                    source=self.name,
                    external_actor_id=str(owner_cik),
                    actor_name=owner_name.title(),
                    actor_type="insider",
                    symbol=symbol,
                    side="buy" if code == "P" else "sell",
                    transaction_date=tx_date,
                    disclosed_at=updated,
                    price=price,
                    quantity=qty,
                    amount_low=(price or 0) * (qty or 0) or None,
                    amount_high=(price or 0) * (qty or 0) or None,
                    raw={"role": role, "code": code, "filing": index_url},
                )
            )
        return out


class Sec13FSource(DisclosureSource):
    """13F-Positionen eines Fonds.

    Quartalsweise und mit 45 Tagen Verzug, also nichts fuer schnelle Kopien.
    Taugt aber, um zu pruefen, ob ein Signal von mehreren Seiten gestuetzt wird,
    und um langfristige Positionen zu spiegeln.
    """

    name = "sec_13f"
    SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]:
        ciks: list[str] = [str(c).zfill(10) for c in self.options.get("ciks", [])]
        if not ciks:
            return
        with HttpClient() as client:
            for cik in ciks:
                try:
                    sub = client.json(self.SUBMISSIONS.format(cik=cik))
                except Exception as exc:  # noqa: BLE001
                    log.warning("13F %s: %s", cik, exc)
                    continue
                name = sub.get("name", cik)
                recent = sub.get("filings", {}).get("recent", {})
                forms = recent.get("form", [])
                for idx, form in enumerate(forms):
                    if form != "13F-HR":
                        continue
                    filed = to_utc(recent.get("filingDate", [None] * len(forms))[idx])
                    if since and filed and filed < since:
                        continue
                    accession = recent.get("accessionNumber", [])[idx].replace("-", "")
                    url = (
                        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/index.json"
                    )
                    yield from self._parse_holdings(client, url, name, cik, filed)
                    break  # nur das juengste Quartal

    def _parse_holdings(
        self,
        client: HttpClient,
        index_url: str,
        name: str,
        cik: str,
        filed: dt.datetime | None,
    ) -> Iterable[RawTrade]:
        try:
            listing = client.json(index_url)
        except Exception:  # noqa: BLE001
            return
        base = index_url.rsplit("/", 1)[0]
        table = next(
            (
                i["name"]
                for i in listing.get("directory", {}).get("item", [])
                if i.get("name", "").endswith(".xml") and "info" in i.get("name", "").lower()
            ),
            None,
        )
        if not table:
            return
        resp = client.get(f"{base}/{table}")
        root = ET.fromstring(resp.content)
        for info in root.iter():
            if not info.tag.endswith("infoTable"):
                continue
            issuer = _local(info, "nameOfIssuer")
            value = _local(info, "value")
            if not issuer:
                continue
            yield RawTrade(
                source=self.name,
                external_actor_id=cik,
                actor_name=name,
                actor_type="fund",
                symbol=None,  # 13F nennt CUSIP, Mapping passiert in der Anreicherung
                side="buy",
                transaction_date=(filed or dt.datetime.now(dt.UTC)).date(),
                disclosed_at=filed,
                amount_low=float(value) * 1000 if value and value.isdigit() else None,
                raw={"issuer": issuer, "cusip": _local(info, "cusip")},
            )


def _text(node: ET.Element, path: str) -> str | None:
    el = node.find(f"{path}/value")
    if el is not None and el.text:
        return el.text
    el = node.find(path)
    return el.text if el is not None else None


def _val(node: ET.Element, path: str) -> float | None:
    raw = _text(node, path)
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _local(node: ET.Element, tag: str) -> str | None:
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1] == tag:
            return (child.text or "").strip()
    return None


def _owner_role(relation: ET.Element | None) -> str:
    if relation is None:
        return "insider"
    roles = []
    if (relation.findtext("isDirector") or "0") in ("1", "true"):
        roles.append("director")
    if (relation.findtext("isOfficer") or "0") in ("1", "true"):
        roles.append(relation.findtext("officerTitle") or "officer")
    if (relation.findtext("isTenPercentOwner") or "0") in ("1", "true"):
        roles.append("10pct_owner")
    return ",".join(roles) or "insider"
