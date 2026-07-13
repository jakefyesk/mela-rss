"""recipe-scrapers wrapper: HTML -> normalized Recipe.

Splits the pure ``_from_html`` step (unit-testable with fixtures) from the
network fetch. ``supported_only=False`` means: use the host-specific scraper
when recipe-scrapers has one (e.g. joshuaweissman, mob), otherwise fall back to
generic schema.org JSON-LD extraction.
"""

from __future__ import annotations

from datetime import datetime, timezone

from bs4 import BeautifulSoup
from recipe_scrapers import scrape_html
from recipe_scrapers._exceptions import (
    NoSchemaFoundInWildMode,
    RecipeSchemaNotFound,
    RecipeScrapersExceptions,
    WebsiteNotImplementedError,
)

from . import normalize
from .config import SourceConfig
from .models import Recipe

_SWALLOW = (RecipeScrapersExceptions, AttributeError, KeyError, TypeError, ValueError)


def _safe(fn, default=""):
    try:
        value = fn()
    except _SWALLOW:
        return default
    return value if value is not None else default


def og_image(html: str) -> str:
    """Fallback image: og:image, then the first prominent <img>.

    Needed for Joshua Weissman, whose recipe-scrapers class does not reliably
    return an image.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001
        return ""
    tag = soup.find("meta", property="og:image") or soup.find(
        "meta", attrs={"name": "twitter:image"}
    )
    if tag and tag.get("content"):
        return tag["content"].strip()
    img = soup.find("img", src=True)
    if img:
        return img["src"].strip()
    return ""


def _parse_datetime(scraper) -> datetime | None:
    # recipe-scrapers exposes the raw schema.org dict via scraper.schema.data.
    data = {}
    schema = getattr(scraper, "schema", None)
    if schema is not None:
        data = getattr(schema, "data", {}) or {}
    raw = data.get("datePublished") or data.get("dateModified")
    if not raw:
        return None
    try:
        from dateutil import parser as dtparser

        dt = dtparser.parse(str(raw))
    except (ValueError, OverflowError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def extract_recipe_from_html(html: str, url: str, cfg: SourceConfig) -> Recipe | None:
    """Pure HTML -> Recipe. Returns None when nothing usable was extracted."""
    try:
        s = scrape_html(html, org_url=url, supported_only=False, best_image=True)
    except (NoSchemaFoundInWildMode, RecipeSchemaNotFound, WebsiteNotImplementedError):
        return None
    except RecipeScrapersExceptions:
        return None

    title = _safe(s.title).strip()
    instructions = normalize.steps_to_mela(_safe(s.instructions))
    ingredients = normalize.ingredients_to_mela(
        _safe(s.ingredient_groups, []), _safe(s.ingredients, [])
    )
    # A recipe with neither ingredients nor steps is a failed extraction — skip it.
    if not title or (not instructions and not ingredients):
        return None

    prep = normalize.minutes_or_none(_safe(s.prep_time, None))
    cook = normalize.minutes_or_none(_safe(s.cook_time, None))
    total = normalize.minutes_or_none(_safe(s.total_time, None))

    image = (_safe(s.image) or "").strip() or og_image(html)

    author = (cfg.rehost_author or _safe(s.author) or "").strip()

    return Recipe(
        dedup_key=normalize.make_dedup_key(cfg.name, url),
        source=cfg.name,
        source_url=normalize.canonicalize_url(url),
        mode=cfg.mode,
        category=cfg.category,
        title=title,
        text=_safe(s.description).strip(),
        ingredients=ingredients,
        instructions=instructions,
        nutrition=normalize.format_nutrients(_safe(s.nutrients, {})),
        yield_=_safe(s.yields).strip(),
        prep_time=normalize.iso8601_from_minutes(prep),
        cook_time=normalize.iso8601_from_minutes(cook),
        total_time=normalize.iso8601_from_minutes(total),
        prep_minutes=prep,
        cook_minutes=cook,
        total_minutes=total,
        categories=normalize.split_categories(_safe(s.category), _safe(s.keywords)),
        cuisine=_safe(s.cuisine).strip(),
        image_url=image,
        author=author,
        published_at=_parse_datetime(s),
    )


def extract_recipe(url: str, cfg: SourceConfig, http) -> Recipe | None:
    """Fetch `url` and extract a Recipe. Network errors bubble up to the caller
    (build.py logs-and-skips per source)."""
    html = http.get(url)
    return extract_recipe_from_html(html, url, cfg)
