"""A small, polite HTTP client with retries and an on-disk cache.

Kept behind a tiny interface so tests can inject a fake (see tests/). The cache
means re-runs don't re-fetch unchanged pages, which is both faster and kinder to
source sites.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import requests

DEFAULT_UA = "mela-rss/0.1 (+https://github.com/jakefyesk/mela-rss)"


class HttpError(RuntimeError):
    pass


class Http:
    def __init__(
        self,
        user_agent: str = DEFAULT_UA,
        delay_seconds: float = 1.0,
        cache_dir: str | Path | None = ".httpcache",
        timeout: int = 30,
        max_retries: int = 3,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self.delay = delay_seconds
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request_at = 0.0

    # -- cache helpers -----------------------------------------------------
    def _cache_path(self, url: str, suffix: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}{suffix}"

    def _throttle(self) -> None:
        if self.delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    # -- fetch -------------------------------------------------------------
    def _fetch(self, url: str, binary: bool, headers: dict | None = None) -> bytes:
        suffix = ".bin" if binary else ".txt"
        cache_path = self._cache_path(url, suffix)
        if cache_path and cache_path.exists():
            return cache_path.read_bytes()

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=self.timeout, headers=headers)
                self._last_request_at = time.monotonic()
                resp.raise_for_status()
                content = resp.content
                if cache_path:
                    # Write atomically so an interrupted run can't leave a
                    # truncated cache entry that would be served forever.
                    tmp = cache_path.with_name(cache_path.name + ".tmp")
                    tmp.write_bytes(content)
                    tmp.replace(cache_path)
                return content
            except requests.RequestException as exc:  # noqa: PERF203
                last_exc = exc
                self._last_request_at = time.monotonic()
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 16))
        raise HttpError(f"GET {url} failed after {self.max_retries} tries: {last_exc}")

    def get(self, url: str, headers: dict | None = None) -> str:
        raw = self._fetch(url, binary=False, headers=headers)
        return raw.decode("utf-8", errors="replace")

    def get_bytes(self, url: str, headers: dict | None = None) -> bytes:
        return self._fetch(url, binary=True, headers=headers)
