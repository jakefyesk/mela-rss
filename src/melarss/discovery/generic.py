"""Config-driven adapter + factory.

GenericAdapter dispatches discovery on cfg.discovery and extracts via
recipe-scrapers. Instagram uses its own adapter (caption parsing).
"""

from __future__ import annotations

from datetime import datetime

from .. import normalize
from ..config import SourceConfig
from ..extract import extract_recipe
from ..models import Recipe
from . import index_page, mob_chef, native_feed, sitemap
from .instagram import InstagramAdapter
from .mindlink import MindLinkAdapter


class GenericAdapter:
    def __init__(self, cfg: SourceConfig, http, *, backfill: bool = False) -> None:
        self.cfg = cfg
        self.http = http
        self.name = cfg.name
        self.category = cfg.category
        self.backfill = backfill
        # ref URL -> published-date hint from discovery (sitemap <lastmod> /
        # feed <pubDate>), used when the page's own JSON-LD has no date. Rebuilt
        # each discover() call.
        self.date_hints: dict[str, datetime] = {}

    def _limit(self) -> int | None:
        if self.backfill and self.cfg.backfill_limit:
            return self.cfg.backfill_limit
        # steady-state: fetch a bit more than max_new_per_run so we still see
        # already-known items (cheap, cached) without unbounded crawling.
        return max(self.cfg.max_new_per_run * 3, 30)

    def discover(self) -> list[str]:
        d = self.cfg.discovery
        limit = self._limit()
        self.date_hints = {}
        if d == "sitemap":
            if not self.cfg.sitemap:
                return []
            entries = sitemap.sitemap_entries(
                self.cfg.sitemap, self.cfg.url_pattern, self.http, limit=limit
            )
            return self._collect(entries)
        if d == "native_feed":
            if not self.cfg.feed_url:
                return []
            entries = _filter_entries(
                native_feed.native_feed_entries(self.cfg.feed_url, self.http), self.cfg.url_pattern
            )
            return self._collect(entries[:limit] if limit else entries)
        if d == "chef_page":
            if not self.cfg.url:
                return []
            urls = _filter(mob_chef.mob_chef_recipe_urls(self.cfg.url, self.http), self.cfg.url_pattern)
            return urls[:limit] if limit else urls
        if d == "index_page":
            if not self.cfg.url:
                return []
            return index_page.index_page_urls(
                self.cfg.url,
                self.cfg.url_pattern,
                self.http,
                extra_index_urls=tuple(self.cfg.index_urls or ()),
                limit=limit,
            )
        return []

    def _collect(self, entries: list[tuple[str, str | None]]) -> list[str]:
        """Record each ref's published-date hint and return just the URLs."""
        urls: list[str] = []
        for url, raw_date in entries:
            urls.append(url)
            dt = normalize.parse_date(raw_date)
            if dt is not None:
                self.date_hints[url] = dt
        return urls

    def fetch_and_parse(self, ref: str) -> Recipe | None:
        return extract_recipe(ref, self.cfg, self.http)


def _filter(urls: list[str], pattern: str | None) -> list[str]:
    if not pattern:
        return urls
    import re

    rx = re.compile(pattern)
    return [u for u in urls if rx.search(u)]


def _filter_entries(
    entries: list[tuple[str, str | None]], pattern: str | None
) -> list[tuple[str, str | None]]:
    if not pattern:
        return entries
    import re

    rx = re.compile(pattern)
    return [(u, d) for u, d in entries if rx.search(u)]


def make_adapter(cfg: SourceConfig, http, *, backfill: bool = False):
    if cfg.discovery == "instagram":
        return InstagramAdapter(cfg, http)
    if cfg.discovery == "mindlink":
        return MindLinkAdapter(cfg, http)
    return GenericAdapter(cfg, http, backfill=backfill)
