"""Config-driven adapter + factory.

GenericAdapter dispatches discovery on cfg.discovery and extracts via
recipe-scrapers. Instagram uses its own adapter (caption parsing).
"""

from __future__ import annotations

from ..config import SourceConfig
from ..extract import extract_recipe
from ..models import Recipe
from . import index_page, mob_chef, native_feed, sitemap
from .instagram import InstagramAdapter


class GenericAdapter:
    def __init__(self, cfg: SourceConfig, http, *, backfill: bool = False) -> None:
        self.cfg = cfg
        self.http = http
        self.name = cfg.name
        self.category = cfg.category
        self.backfill = backfill

    def _limit(self) -> int | None:
        if self.backfill and self.cfg.backfill_limit:
            return self.cfg.backfill_limit
        # steady-state: fetch a bit more than max_new_per_run so we still see
        # already-known items (cheap, cached) without unbounded crawling.
        return max(self.cfg.max_new_per_run * 3, 30)

    def discover(self) -> list[str]:
        d = self.cfg.discovery
        limit = self._limit()
        if d == "sitemap":
            if not self.cfg.sitemap:
                return []
            return sitemap.sitemap_urls(self.cfg.sitemap, self.cfg.url_pattern, self.http, limit=limit)
        if d == "native_feed":
            if not self.cfg.feed_url:
                return []
            urls = _filter(native_feed.native_feed_urls(self.cfg.feed_url, self.http), self.cfg.url_pattern)
            return urls[:limit] if limit else urls
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

    def fetch_and_parse(self, ref: str) -> Recipe | None:
        return extract_recipe(ref, self.cfg, self.http)


def _filter(urls: list[str], pattern: str | None) -> list[str]:
    if not pattern:
        return urls
    import re

    rx = re.compile(pattern)
    return [u for u in urls if rx.search(u)]


def make_adapter(cfg: SourceConfig, http, *, backfill: bool = False):
    if cfg.discovery == "instagram":
        return InstagramAdapter(cfg, http)
    return GenericAdapter(cfg, http, backfill=backfill)
