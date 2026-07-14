"""Adapter protocol. GenericAdapter and InstagramAdapter both satisfy it."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..models import Recipe


class SourceAdapter(Protocol):
    name: str
    # ref -> published-date hint, populated by discover() when the source
    # exposes per-item dates (sitemap <lastmod>, feed <pubDate>). Empty otherwise.
    date_hints: dict[str, datetime]

    def discover(self) -> list[str]:
        """Return recipe refs (URLs, or platform ids), bounded by config limits."""
        ...

    def fetch_and_parse(self, ref: str) -> Recipe | None:
        """Turn a ref into a normalized Recipe, or None to skip."""
        ...
