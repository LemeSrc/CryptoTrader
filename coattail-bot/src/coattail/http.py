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

CONTACT = os.getenv("COATTAIL_CONTACT", "coattail-bot (contact: set COATTAIL_CONTACT)")
USER_AGENT = f"coattail-bot/0.1 {CONTACT}"


class RateLimiter:
    """Minimaler Token-Bucket pro Host."""

    def __init__(self, min_interval: float = 1.0) -> None:
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            last = self._last.get(host, 0.0)
            delta = time.monotonic() - last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta + random.uniform(0, 0.25))
            self._last[host] = time.monotonic()


_limiter = RateLimiter(min_interval=float(os.getenv("COATTAIL_MIN_REQUEST_INTERVAL", "1.0")))


class HttpClient:
    def __init__(self, timeout: float = 30.0, headers: dict[str, str] | None = None) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, **(headers or {})},
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
