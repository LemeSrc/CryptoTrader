"""Nicht-politische Vorbilder: verifizierte Trader auf Social-Trading-Plattformen
und Wallets, deren Fills direkt on-chain nachlesbar sind.

Invo (app.invoapp.com) veroeffentlicht keine dokumentierte oeffentliche API.
Deshalb drei Wege, absteigend nach Sauberkeit:

  1. api    Ein offizieller Endpunkt, sobald es einen gibt. Basis-URL und
            Pfade stehen in der Konfiguration, es muss also kein Code
            angefasst werden.
  2. cookie Das interne JSON-Backend mit der eigenen Session. Funktioniert,
            solange man das eigene Konto benutzt und die Nutzungsbedingungen
            der Plattform das zulaesst. Bitte vorher pruefen.
  3. chain  Der interessanteste Weg. Invo-Trader fuehren auf Hyperliquid aus,
            und Hyperliquid legt jeden Fill jeder Wallet offen. Wer die Wallet
            einer Person kennt, braucht die Plattform gar nicht mehr: der
            Track Record ist dann nicht behauptet, sondern nachgerechnet.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable

from ..http import HttpClient
from ..util import clean_symbol, to_date, to_utc
from .base import DisclosureSource, RawTrade, normalize_side

log = logging.getLogger(__name__)


class InvoSource(DisclosureSource):
    """Leaderboard und Trade-Feed einer Social-Trading-Plattform."""

    name = "invo"

    def available(self) -> tuple[bool, str]:
        mode = self.options.get("mode", "chain")
        if mode == "api" and not self.secrets.has("invo_api_key"):
            return False, "invo_api_key fehlt"
        if mode == "cookie" and not self.secrets.has("invo_session_cookie"):
            return False, "invo_session_cookie fehlt"
        if mode == "chain" and not self.options.get("wallets"):
            return False, "keine wallets in der Konfiguration"
        return True, f"bereit (Modus {mode})"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]:
        mode = self.options.get("mode", "chain")
        if mode == "chain":
            yield from HyperliquidSource(self.config, self.secrets, self.source_config).fetch(since)
            return
        base = self.options.get("base_url", "https://app.invoapp.com/api")
        headers = {"Accept": "application/json"}
        if mode == "api":
            headers["Authorization"] = f"Bearer {self.secrets.invo_api_key}"
        else:
            headers["Cookie"] = self.secrets.invo_session_cookie or ""

        follow = self.options.get("follow", [])
        trades_path = self.options.get("trades_path", "/users/{user}/trades")
        with HttpClient(headers=headers) as client:
            for user in follow:
                url = base + trades_path.format(user=user)
                try:
                    payload = client.json(url, params=self.options.get("params", {}))
                except Exception as exc:  # noqa: BLE001
                    log.warning("invo: %s nicht abrufbar (%s)", user, exc)
                    continue
                rows = payload if isinstance(payload, list) else payload.get("data", [])
                for row in rows:
                    trade = self._parse(row, user)
                    if trade and (not since or not trade.disclosed_at or trade.disclosed_at >= since):
                        yield trade

    def _parse(self, row: dict, user: str) -> RawTrade | None:
        symbol = clean_symbol(row.get("symbol") or row.get("ticker") or row.get("coin"))
        side = normalize_side(row.get("side") or row.get("action") or row.get("type"))
        when = to_utc(row.get("timestamp") or row.get("createdAt") or row.get("time"))
        if not symbol or not side or not when:
            return None
        return RawTrade(
            source=self.name,
            external_actor_id=str(row.get("userId") or user),
            actor_name=str(row.get("username") or user),
            actor_type="trader",
            symbol=symbol,
            asset_class=self.options.get("asset_class", "crypto"),
            side=side,
            transaction_date=when.date(),
            disclosed_at=when,
            price=_f(row.get("price") or row.get("entryPrice")),
            quantity=_f(row.get("size") or row.get("quantity")),
            stop_loss=_f(row.get("stopLoss") or row.get("sl")),
            take_profit=_f(row.get("takeProfit") or row.get("tp")),
            raw=row,
        )


class HyperliquidSource(DisclosureSource):
    """Fills beliebiger Wallets, direkt vom Hyperliquid-Info-Endpunkt.

    Kein Key, kein Konto, keine Zustimmung noetig: die Daten liegen ohnehin
    offen auf der Kette. Damit laesst sich jeder behauptete Track Record
    nachrechnen statt ihm zu glauben.
    """

    name = "hyperliquid"
    INFO = "https://api.hyperliquid.xyz/info"

    def available(self) -> tuple[bool, str]:
        if not self.options.get("wallets"):
            return False, "keine wallets in der Konfiguration"
        return True, "bereit"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]:
        wallets: dict[str, str] = self._wallets()
        start_ms = int((since or dt.datetime.now(dt.UTC) - dt.timedelta(days=365)).timestamp() * 1000)
        with HttpClient(headers={"Content-Type": "application/json"}) as client:
            for address, label in wallets.items():
                body = {"type": "userFillsByTime", "user": address, "startTime": start_ms}
                try:
                    resp = client.post(self.INFO, json=body)
                    resp.raise_for_status()
                    fills = resp.json()
                except Exception as exc:  # noqa: BLE001
                    log.warning("hyperliquid %s: %s", label, exc)
                    continue
                if not isinstance(fills, list):
                    continue
                log.info("hyperliquid %s: %d Fills", label, len(fills))
                for fill in fills:
                    trade = self._parse_fill(fill, address, label)
                    if trade:
                        yield trade

    def _wallets(self) -> dict[str, str]:
        raw = self.options.get("wallets", [])
        if isinstance(raw, dict):
            return {k: v for k, v in raw.items()}
        out = {}
        for item in raw:
            if isinstance(item, dict):
                out[item["address"]] = item.get("label", item["address"][:10])
            else:
                out[item] = str(item)[:10]
        return out

    def _parse_fill(self, fill: dict, address: str, label: str) -> RawTrade | None:
        symbol = clean_symbol(fill.get("coin"))
        raw_side = fill.get("dir") or fill.get("side")
        side = self._side(raw_side)
        ts = fill.get("time")
        if not symbol or not side or not ts:
            return None
        when = dt.datetime.fromtimestamp(int(ts) / 1000, tz=dt.UTC)
        return RawTrade(
            source=self.name,
            external_actor_id=address,
            actor_name=label,
            actor_type="trader",
            symbol=symbol,
            asset_class="crypto",
            side=side,
            transaction_date=when.date(),
            disclosed_at=when,
            price=_f(fill.get("px")),
            quantity=_f(fill.get("sz")),
            raw={
                "dir": raw_side,
                "closedPnl": fill.get("closedPnl"),
                "fee": fill.get("fee"),
                "startPosition": fill.get("startPosition"),
                "hash": fill.get("hash"),
            },
        )

    @staticmethod
    def _side(direction: str | None) -> str | None:
        d = (direction or "").lower()
        if "open long" in d or "buy" in d or d == "b":
            return "buy"
        if "close short" in d:
            return "buy"
        if "open short" in d or "sell" in d or d == "a":
            return "sell"
        if "close long" in d:
            return "sell"
        return None


class GenericLeaderboardSource(DisclosureSource):
    """Bausatz fuer jede Plattform mit JSON-Leaderboard.

    Statt fuer jede Seite eine eigene Klasse zu schreiben, beschreibt die
    Konfiguration, wo die Felder liegen. Damit laesst sich zum Beispiel ein
    Binance-Copy-Trading-Portfolio oder ein eigener Discord-Export anbinden,
    ohne den Bot anzufassen.
    """

    name = "leaderboard"

    def available(self) -> tuple[bool, str]:
        if not self.options.get("url"):
            return False, "keine url konfiguriert"
        return True, "bereit"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawTrade]:
        url = self.options["url"]
        mapping: dict[str, str] = self.options.get("map", {})
        root_key = self.options.get("root")
        headers = self.options.get("headers", {})
        with HttpClient(headers=headers) as client:
            payload = client.json(url, params=self.options.get("params", {}))
        rows = payload.get(root_key, []) if root_key else payload
        for row in rows or []:
            symbol = clean_symbol(_dig(row, mapping.get("symbol", "symbol")))
            side = normalize_side(_dig(row, mapping.get("side", "side")))
            when = to_utc(_dig(row, mapping.get("time", "time")))
            if not symbol or not side or not when:
                continue
            if since and when < since:
                continue
            yield RawTrade(
                source=self.options.get("label", self.name),
                external_actor_id=str(_dig(row, mapping.get("actor_id", "trader")) or "unknown"),
                actor_name=str(_dig(row, mapping.get("actor_name", "trader")) or "unknown"),
                actor_type="trader",
                symbol=symbol,
                asset_class=self.options.get("asset_class", "crypto"),
                side=side,
                transaction_date=to_date(when),
                disclosed_at=when,
                price=_f(_dig(row, mapping.get("price", "price"))),
                quantity=_f(_dig(row, mapping.get("quantity", "size"))),
                stop_loss=_f(_dig(row, mapping.get("stop_loss", "stopLoss"))),
                take_profit=_f(_dig(row, mapping.get("take_profit", "takeProfit"))),
                raw=row,
            )


def _dig(row: dict, path: str):
    cur = row
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _f(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None
