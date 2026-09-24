"""Beitraege der beobachteten Personen.

Warum ueberhaupt Posts, wenn es doch Pflichtmeldungen gibt: die Meldung kommt
Wochen spaeter, der Post kommt sofort. Ein Satz zu Zoellen, Chipexporten oder
einem Uebernahmeverbot bewegt Kurse, bevor irgendein Formular eingereicht ist.
Die Meldungen sagen, wem man folgen sollte. Die Posts sagen, wann.

Bluesky ist die einzige der grossen Plattformen mit einer wirklich offenen,
kostenlosen und stabilen Leseschnittstelle. Der Rest braucht entweder Geld
(X) oder laeuft ueber Umwege (RSS-Spiegel, Mastodon-kompatible Endpunkte).
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
                account_id = account.get("id") if isinstance(account, dict) else account
                label = account.get("handle") if isinstance(account, dict) else str(account)
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


class XSource(PostSource):
    """X/Twitter API v2. Braucht ein bezahltes Kontingent."""

    name = "x"
    requires = ("x_bearer_token",)
    BASE = "https://api.twitter.com/2"

    def fetch(self, since: dt.datetime | None = None) -> Iterable[RawPost]:
        headers = {"Authorization": f"Bearer {self.secrets.x_bearer_token}"}
        users: list[dict] = self.options.get("users", [])
        with HttpClient(headers=headers) as client:
            for user in users:
                uid = user.get("id")
                handle = user.get("handle", str(uid))
                if not uid:
                    continue
                params = {
                    "max_results": self.options.get("limit", 20),
                    "tweet.fields": "created_at,text,entities",
                    "exclude": "retweets,replies",
                }
                if since:
                    params["start_time"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
                try:
                    payload = client.json(f"{self.BASE}/users/{uid}/tweets", params=params)
                except Exception as exc:  # noqa: BLE001
                    log.warning("x %s: %s", handle, exc)
                    continue
                for row in payload.get("data", []):
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
        for feed in self.options.get("feeds", []):
            url = feed["url"] if isinstance(feed, dict) else feed
            label = feed.get("label", url) if isinstance(feed, dict) else url
            try:
                parsed = feedparser.parse(url)
            except Exception as exc:  # noqa: BLE001
                log.warning("rss %s: %s", label, exc)
                continue
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
