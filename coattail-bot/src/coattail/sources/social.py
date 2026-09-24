"""Beitraege der beobachteten Personen.

Warum ueberhaupt Posts, wenn es doch Pflichtmeldungen gibt: die Meldung kommt
Wochen spaeter, der Post kommt sofort. Ein Satz zu Zoellen, Chipexporten oder
einem Uebernahmeverbot bewegt Kurse, bevor irgendein Formular eingereicht ist.
Die Meldungen sagen, wem man folgen sollte. Die Posts sagen, wann.

Bluesky ist die einzige der grossen Plattformen mit einer wirklich offenen,
kostenlosen und stabilen Leseschnittstelle. Der Rest braucht entweder Geld
(X, pro gelesenem Post) oder laeuft ueber Umwege: Truth Social ueber die
Mastodon-kompatible Schnittstelle oder ein RSS-Archiv, Pressemitteilungen
und Verfuegungen ueber RSS.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable

import feedparser

from ..http import HttpClient
from ..util import to_utc
from .base import PostSource, RawPost

log = logging.getLogger(__name__)


class BlueskySource(PostSource):
    """Oeffentliche AT-Protocol-Schnittstelle. Kein Token noetig."""

    name = "bluesky"
    BASE = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"

    def available(self) -> tuple[bool, str]:
        if not self.options.get("handles"):
            return False, "keine handles konfiguriert"
        return True, "bereit"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawPost]:
        limit = int(self.options.get("limit", 40))
        with HttpClient() as client:
            for handle in self.options.get("handles", []):
                try:
                    payload = client.json(
                        self.BASE, params={"actor": handle, "limit": limit, "filter": "posts_no_replies"}
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("bluesky %s: %s", handle, exc)
                    continue
                for item in payload.get("feed", []):
                    post = item.get("post", {})
                    record = post.get("record", {})
                    created = to_utc(record.get("createdAt"))
                    text = record.get("text", "")
                    if not created or not text:
                        continue
                    if since and created < since:
                        continue
                    author = post.get("author", {})
                    uri = post.get("uri", "")
                    yield RawPost(
                        platform=self.name,
                        external_id=uri,
                        author=author.get("handle", handle),
                        author_id=author.get("did"),
                        text=text,
                        posted_at=created,
                        url=(
                            f"https://bsky.app/profile/{author.get('handle', handle)}"
                            f"/post/{uri.rsplit('/', 1)[-1]}"
                        ),
                        raw={"displayName": author.get("displayName")},
                    )


class MastodonApiSource(PostSource):
    """Mastodon-kompatible Endpunkte.

    Truth Social spricht dieselbe API. Ob der Abruf durchgeht, haengt am
    Bot-Schutz davor. Wenn nicht: RSS-Spiegel eintragen, gleiche Wirkung.
    """

    name = "mastodon"

    def available(self) -> tuple[bool, str]:
        if not self.options.get("accounts"):
            return False, "keine accounts konfiguriert"
        return True, "bereit"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawPost]:
        base = self.options.get("base_url", "https://truthsocial.com")
        headers = {"Accept": "application/json"}
        if token := self.options.get("token"):
            headers["Authorization"] = f"Bearer {token}"
        with HttpClient(headers=headers) as client:
            for account in self.options.get("accounts", []):
                account_id = account.get("id") if isinstance(account, dict) else None
                label = str(
                    (account.get("handle") if isinstance(account, dict) else account) or account_id
                ).lstrip("@")
                account_id = account_id or self._lookup(client, base, label)
                if not account_id:
                    continue
                url = f"{base}/api/v1/accounts/{account_id}/statuses"
                try:
                    rows = client.json(url, params={"limit": self.options.get("limit", 40)})
                except Exception as exc:  # noqa: BLE001
                    log.warning("mastodon %s: %s", label, exc)
                    continue
                for row in rows or []:
                    created = to_utc(row.get("created_at"))
                    text = _strip_html(row.get("content", ""))
                    if not created or not text:
                        continue
                    if since and created < since:
                        continue
                    yield RawPost(
                        platform=self.name,
                        external_id=str(row.get("id")),
                        author=label,
                        author_id=str(account_id),
                        text=text,
                        posted_at=created,
                        url=row.get("url"),
                        raw={"reblog": bool(row.get("reblog"))},
                    )


    def _lookup(self, client: HttpClient, base: str, handle: str) -> str | None:
        """Handle zu Konto-ID. Wird einmal pro Prozess nachgeschlagen."""
        cache: dict[str, str] = self.__dict__.setdefault("_ids", {})
        if handle in cache:
            return cache[handle]
        if handle.isdigit():
            return handle
        try:
            payload = client.json(f"{base}/api/v1/accounts/lookup", params={"acct": handle})
        except Exception as exc:  # noqa: BLE001
            log.warning("mastodon: Konto %s nicht aufloesbar: %s", handle, exc)
            return None
        account_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
        if account_id:
            cache[handle] = account_id
        return account_id or None


class XSource(PostSource):
    """X/Twitter API v2.

    Lesen kostet seit 2026 pro abgerufenem Post (Pay-per-use, kein
    Gratiskontingent mehr fuer neue Entwickler). Deshalb drei Bremsen: der
    Abruf fragt nur nach Posts seit dem letzten Lauf (start_time), die
    Obergrenze pro Konto und Lauf ist klein, und die Kennung eines Kontos wird
    nur einmal nachgeschlagen und dann im Speicher gehalten.

    In der Konfiguration genuegt der Handle. Die numerische ID ist optional
    und spart den einmaligen Nachschlag.
    """

    name = "x"
    requires = ("x_bearer_token",)
    BASE = "https://api.twitter.com/2"

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self._ids: dict[str, str] = {}

    def available(self) -> tuple[bool, str]:
        ok, reason = super().available()
        if not ok:
            return ok, reason
        if not self.options.get("users"):
            return False, "keine users konfiguriert"
        return True, "bereit (kostet pro gelesenem Post)"

    def _users(self) -> list[tuple[str | None, str]]:
        out: list[tuple[str | None, str]] = []
        for user in self.options.get("users", []):
            if isinstance(user, str):
                out.append((None, user.lstrip("@")))
            elif isinstance(user, dict):
                handle = str(user.get("handle") or user.get("id") or "").lstrip("@")
                out.append((str(user["id"]) if user.get("id") else None, handle))
        return out

    def _resolve(self, client: HttpClient, handle: str) -> str | None:
        if handle in self._ids:
            return self._ids[handle]
        try:
            payload = client.json(f"{self.BASE}/users/by/username/{handle}")
        except Exception as exc:  # noqa: BLE001
            log.warning("x: Konto %s nicht aufloesbar: %s", handle, exc)
            return None
        uid = (payload.get("data") or {}).get("id")
        if uid:
            self._ids[handle] = str(uid)
            log.info("x: %s hat die ID %s", handle, uid)
        return uid

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawPost]:
        headers = {"Authorization": f"Bearer {self.secrets.x_bearer_token}"}
        # Die Schnittstelle verlangt 5 bis 100 Ergebnisse pro Seite.
        limit = max(5, min(100, int(self.options.get("limit", 10))))
        # Ohne Cursor nicht die ganze Historie ziehen, das waere teuer und
        # fuer Signale ohnehin zu alt.
        floor = dt.datetime.now(dt.UTC) - dt.timedelta(hours=int(self.options.get("first_run_hours", 6)))
        start = max(since, floor) if since else floor
        with HttpClient(headers=headers) as client:
            for uid, handle in self._users():
                uid = uid or self._resolve(client, handle)
                if not uid:
                    continue
                params = {
                    "max_results": limit,
                    "tweet.fields": "created_at,text,entities",
                    "exclude": "retweets,replies",
                    "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                try:
                    payload = client.json(f"{self.BASE}/users/{uid}/tweets", params=params)
                except Exception as exc:  # noqa: BLE001
                    log.warning("x %s: %s", handle, exc)
                    continue
                for row in payload.get("data", []) or []:
                    created = to_utc(row.get("created_at"))
                    if not created:
                        continue
                    yield RawPost(
                        platform=self.name,
                        external_id=str(row["id"]),
                        author=handle,
                        author_id=str(uid),
                        text=row.get("text", ""),
                        posted_at=created,
                        url=f"https://x.com/{handle}/status/{row['id']}",
                    )


class RssSource(PostSource):
    """Alles, was einen Feed hat.

    Deckt erstaunlich viel ab: Pressemitteilungen des Weissen Hauses,
    Ausschussmeldungen, Nitter-Spiegel einzelner X-Konten, Substacks von
    Tradern. Ein Eintrag in der Konfiguration, keine Zeile Code.
    """

    name = "rss"

    def available(self) -> tuple[bool, str]:
        if not self.options.get("feeds"):
            return False, "keine feeds konfiguriert"
        return True, "bereit"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawPost]:
        # Abruf ueber den eigenen Client statt feedparser.parse(url): der hat
        # kein Zeitlimit, und ein haengender Server wuerde den Job blockieren.
        headers = {
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"
        }
        with HttpClient(timeout=20.0, headers=headers) as client:
            for feed in self.options.get("feeds", []):
                url = feed["url"] if isinstance(feed, dict) else feed
                label = feed.get("label", url) if isinstance(feed, dict) else url
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    parsed = feedparser.parse(resp.content)
                except Exception as exc:  # noqa: BLE001
                    log.warning("rss %s: %s", label, exc)
                    continue
                if not parsed.entries and getattr(parsed, "bozo", False):
                    log.warning("rss %s: kein gueltiger Feed (%s)", label, parsed.get("bozo_exception"))
                    continue
                yield from self._entries(parsed, label, since)

    def _entries(self, parsed, label: str, since: dt.datetime | None) -> Iterable[RawPost]:  # noqa: ANN001
        for entry in parsed.entries:
            created = to_utc(entry.get("published") or entry.get("updated"))
            if not created or (since and created < since):
                continue
            text = _strip_html(entry.get("summary") or entry.get("title") or "")
            if not text:
                continue
            yield RawPost(
                platform=self.name,
                external_id=entry.get("id") or entry.get("link") or f"{label}:{created}",
                author=label,
                text=f"{entry.get('title', '')}\n{text}".strip(),
                posted_at=created,
                url=entry.get("link"),
            )


def _strip_html(html: str) -> str:
    if "<" not in html:
        return html.strip()
    try:
        from selectolax.parser import HTMLParser

        return HTMLParser(html).text(separator=" ").strip()
    except Exception:  # noqa: BLE001
        import re

        return re.sub(r"<[^>]+>", " ", html).strip()
