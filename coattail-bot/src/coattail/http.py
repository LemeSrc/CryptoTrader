"""Ein gemeinsamer HTTP-Client fuer alle Quellen.

Wichtig fuer Scraper-artige Quellen: hoefliches Verhalten. Ein Request pro
Intervall, echter User-Agent mit Kontaktadresse (die SEC verlangt das sogar),
Wiederholung mit Backoff statt Dauerfeuer.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger(__name__)

def user_agent() -> str:
    """Erst beim Aufbau des Clients lesen, damit ein spaet geladenes .env greift."""
    contact = os.getenv("COATTAIL_CONTACT", "coattail-bot (contact: set COATTAIL_CONTACT)")
    return f"coattail-bot/0.2 {contact}"


class RateLimiter:
    """Minimaler Token-Bucket pro Host."""

    def __init__(self, min_interval: float = 1.0, per_host: dict[str, float] | None = None) -> None:
        self.min_interval = min_interval
        self.per_host = per_host or {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        interval = self.per_host.get(host, self.min_interval)
        with self._lock:
            last = self._last.get(host, 0.0)
            delta = time.monotonic() - last
            if delta < interval:
                time.sleep(interval - delta + random.uniform(0, interval / 4))
            self._last[host] = time.monotonic()


# Die SEC erlaubt ausdruecklich 10 Abrufe pro Sekunde. Fuenf lassen Luft und
# machen den Form-4-Abruf fuenfmal schneller als die allgemeine Sekunde.
_limiter = RateLimiter(
    min_interval=float(os.getenv("COATTAIL_MIN_REQUEST_INTERVAL", "1.0")),
    per_host={"www.sec.gov": 0.2},
)


class HttpClient:
    def __init__(self, timeout: float = 30.0, headers: dict[str, str] | None = None) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent(), **(headers or {})},
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=2, max=30),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    )
    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        _limiter.wait(httpx.URL(url).host or "")
        resp = self._client.get(url, **kwargs)
        if resp.status_code in (429, 500, 502, 503, 504):
            log.warning("HTTP %s bei %s, versuche erneut", resp.status_code, url)
            resp.raise_for_status()
        return resp

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=2, max=20),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    )
    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        _limiter.wait(httpx.URL(url).host or "")
        resp = self._client.post(url, **kwargs)
        if resp.status_code in (429, 500, 502, 503, 504):
            resp.raise_for_status()
        return resp

    def json(self, url: str, **kwargs: Any) -> Any:
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
