"""Core data model.

``Recipe`` mirrors Mela's ``.melarecipe`` JSON fields so exporting a bundle is a
near-passthrough, plus provenance/metadata used by the feeds and reserved for
the future personalization ("conversational") feed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Mode(str, Enum):
    """How a source's recipes reach Mela.

    LINK_THROUGH: the source's own page has clean Recipe JSON-LD, so the feed
        item links straight to it and Mela extracts natively.
    REHOST: the source has no usable JSON-LD (or no web page at all, e.g.
        Instagram), so we render our own page and point the feed item at it.
    """

    LINK_THROUGH = "link_through"
    REHOST = "rehost"


class Category(str, Enum):
    RECIPE = "recipe"
    COCKTAIL = "cocktail"


@dataclass
class Recipe:
    # --- identity / provenance ---
    dedup_key: str  # stable primary id; also the RSS <guid> and .melarecipe id
    source: str  # sources.yaml `name`
    source_url: str  # canonical original URL (or platform id for Instagram)
    mode: Mode
    category: Category = Category.RECIPE

    # --- Mela-mirroring content fields ---
    title: str = ""
    text: str = ""  # short description -> melarecipe `text`
    ingredients: str = ""  # single \n string; a line starting with '#' is a group header
    instructions: str = ""  # single \n string
    notes: str = ""
    nutrition: str = ""
    yield_: str = ""  # melarecipe `yield`
    prep_time: str = ""  # ISO-8601 duration, e.g. "PT20M"
    cook_time: str = ""
    total_time: str = ""
    categories: list[str] = field(default_factory=list)  # NO commas (Mela restriction)
    cuisine: str = ""

    # --- images ---
    image_url: str = ""  # absolute URL used in JSON-LD `image` + feed (hosted for rehost)
    local_image: str = ""  # docs-relative path when we self-host the image, else ""

    # --- feed / ranking metadata ---
    author: str = ""
    page_url: str = ""  # where the RSS <item> points (== source_url for link_through)
    published_at: datetime | None = None  # real source publish date, if known
    discovered_at: datetime | None = None  # first time WE saw it (set once, persisted)
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    total_minutes: int | None = None

    def mela_id(self) -> str:
        """Required, non-empty melarecipe identifier."""
        return self.dedup_key
