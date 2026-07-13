"""Adapter protocol. GenericAdapter and InstagramAdapter both satisfy it."""

from __future__ import annotations

from typing import Protocol

from ..models import Recipe


class SourceAdapter(Protocol):
    name: str

    def discover(self) -> list[str]:
        """Return recipe refs (URLs, or platform ids), bounded by config limits."""
        ...

    def fetch_and_parse(self, ref: str) -> Recipe | None:
        """Turn a ref into a normalized Recipe, or None to skip."""
        ...
