"""Pure normalization helpers — no network, fully unit-testable.

These turn the loose values recipe-scrapers / caption parsing hand us into the
exact shapes Mela and schema.org want:
  * durations: integer minutes -> ISO-8601 ("PT1H35M")
  * ingredients: list + IngredientGroups -> single "\n" string with "#" headers
  * categories: comma/slash blob -> list of comma-free tags (Mela forbids commas)
  * stable dedup keys from canonicalized URLs
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Exact param names to drop, vs. prefix families. "ref" is exact so we don't
# clobber legitimate params like "reference"/"referrer"/"refresh".
_TRACKING_EXACT = frozenset({"fbclid", "gclid", "igshid", "ref", "mc_cid", "mc_eid"})
_TRACKING_PREFIXES = ("utm_", "mc_")

# Language codes that appear as a leading *locale path prefix* on localized
# storefronts (Shopify Markets, Wix, Next.js i18n, Squarespace). Such a site
# publishes ONE recipe at /blogs/recipes/x, /de/blogs/recipes/x and
# /es/blogs/recipes/x, and lists all three in its sitemap — which is exactly how
# a single recipe reaches the feed three times.
#
# Deliberately an allowlist of locales the web actually localizes into rather
# than all of ISO-639-1: codes like "is"/"be"/"to" are far more likely to be a
# real path segment than a language. Even so this only ever affects the *dedup
# key*; the URL we fetch, store and link to is untouched.
# "uk" and "ca" are omitted on purpose: on an English-language site those are
# far more often region segments (United Kingdom, Canada) than Ukrainian or
# Catalan, and region segments can carry genuinely different content.
_LOCALE_CODES = frozenset(
    """ar bg bn cs da de el en es et fa fi fr he hi hr hu id it ja ko lt lv
    ms nb nl nn no pl pt ro ru sk sl sr sv th tr vi zh""".split()
)
# "de", "pt-br", "zh_hans", "en-US" — a language, optionally a region or script.
# The suffix is a 2-letter region or a named script, NOT any 2-4 letters: the
# looser form matched ordinary recipe paths like "/no-bake/..." ("no" + "bake").
_LOCALE_SEGMENT_RE = re.compile(r"^([a-z]{2})(?:[-_](?:[a-z]{2}|hans|hant|latn|cyrl|arab))?$")


def slugify(text: str, max_len: int = 80) -> str:
    """Filesystem/URL-safe slug. Deterministic (drives stable page URLs)."""
    slug = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "recipe"


def canonicalize_url(url: str) -> str:
    """Deterministic canonical form used for dedup keys.

    Lowercases host, drops the fragment and tracking query params, and strips a
    trailing slash from the path. Query params that are *not* tracking noise are
    kept and sorted so ordering never changes the key.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    kept = []
    for pair in parts.query.split("&"):
        if not pair:
            continue
        key = pair.split("=", 1)[0].lower()
        if key in _TRACKING_EXACT or any(key.startswith(p) for p in _TRACKING_PREFIXES):
            continue
        kept.append(pair)
    query = "&".join(sorted(kept))
    return urlunsplit((scheme, host, path, query, ""))


def strip_locale_prefix(path: str) -> str:
    """Drop a leading locale path segment: "/de/blogs/x" -> "/blogs/x".

    Returns `path` untouched unless the first segment is a known locale code AND
    something remains after it (so "/de" — which could be a real page — and
    non-absolute paths are left alone). Only the first segment is considered:
    locale prefixes are always leftmost.
    """
    if not path.startswith("/"):
        return path
    head, sep, rest = path[1:].partition("/")
    if not sep or not rest:
        return path
    match = _LOCALE_SEGMENT_RE.match(head.lower())
    if not match or match.group(1) not in _LOCALE_CODES:
        return path
    return "/" + rest


def dedup_url(url: str) -> str:
    """Canonical URL used for *identity only* — never for fetching or linking.

    `canonicalize_url` plus locale-prefix folding, so the translated variants of
    one recipe share a dedup key instead of arriving as separate recipes.

    Host and scheme are deliberately left as-is: folding "www." or http/https
    would rewrite the dedup key — i.e. the RSS <guid> — of every recipe already
    published, and Mela would re-import the whole library. Duplicates that
    differ that way are caught downstream by the content fingerprint instead.
    """
    canonical = canonicalize_url(url)
    if not canonical:
        return ""
    parts = urlsplit(canonical)
    path = strip_locale_prefix(parts.path)
    if path == parts.path:
        return canonical
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def make_dedup_key(source: str, url: str) -> str:
    """Stable primary id: sha1(source | dedup url).

    Never derive this from a title, slug, or pubDate — guid drift makes Mela
    re-import everything. `dedup_url` folds locale variants of one page onto one
    key so a translated storefront can't publish the same recipe three times.
    """
    basis = f"{source}|{dedup_url(url)}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def parse_date(value) -> datetime | None:
    """Best-effort parse of a date/datetime string into a tz-aware UTC datetime.

    Accepts the shapes discovery already sees but currently discards: ISO-8601
    sitemap <lastmod> ("2026-06-20", "2026-06-21T12:00:00Z") and RFC-822 feed
    <pubDate> ("Sat, 20 Jun 2026 12:00:00 GMT"). Naive datetimes are assumed
    UTC. Returns None on anything unparseable — callers treat that as "no date".
    """
    if not value:
        return None
    from dateutil import parser as dtparser

    try:
        dt = dtparser.parse(str(value))
    except (ValueError, OverflowError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def minutes_or_none(value) -> int | None:
    """recipe-scrapers returns times as ints (minutes); coerce loosely to int."""
    if value in (None, "", 0):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def iso8601_from_minutes(minutes: int | None) -> str:
    """95 -> 'PT1H35M'.  None/0 -> ''."""
    if not minutes or minutes <= 0:
        return ""
    hours, mins = divmod(int(minutes), 60)
    out = "PT"
    if hours:
        out += f"{hours}H"
    if mins:
        out += f"{mins}M"
    return out if out != "PT" else "PT0M"


def split_categories(*raw: str) -> list[str]:
    """Split comma/slash/newline blobs into deduped, comma-free tags.

    Mela forbids commas inside a category name, so any comma becomes a split
    point. Order-preserving, case-insensitive dedup.
    """
    out: list[str] = []
    seen: set[str] = set()
    for blob in raw:
        if not blob:
            continue
        # recipe-scrapers' keywords() (and some category() impls) return a list.
        if isinstance(blob, (list, tuple, set)):
            blob = ",".join(str(x) for x in blob)
        elif not isinstance(blob, str):
            blob = str(blob)
        for piece in re.split(r"[,/\n|]+", blob):
            tag = piece.strip().strip("#").strip()
            if not tag:
                continue
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                out.append(tag)
    return out


def ingredients_to_mela(groups, flat: list[str] | None = None) -> str:
    """Build Mela's ingredients string from recipe-scrapers output.

    `groups` is a list of objects with `.purpose` (heading or None) and
    `.ingredients` (list[str]). A truthy purpose becomes a "# heading" line.
    Falls back to `flat` when groups are empty or degenerate (single group with
    no purpose == just the flat list).
    """
    lines: list[str] = []
    meaningful = [g for g in (groups or []) if getattr(g, "ingredients", None)]
    has_headers = any((getattr(g, "purpose", None) or "").strip() for g in meaningful)

    if meaningful and (has_headers or len(meaningful) > 1):
        for g in meaningful:
            purpose = (getattr(g, "purpose", None) or "").strip()
            if purpose:
                lines.append(f"# {purpose}")
            for ing in g.ingredients:
                if ing and ing.strip():
                    lines.append(ing.strip())
    else:
        source = flat if flat is not None else (meaningful[0].ingredients if meaningful else [])
        for ing in source or []:
            if ing and ing.strip():
                lines.append(ing.strip())
    return "\n".join(lines)


def steps_to_mela(instructions) -> str:
    """Normalize instructions to a single '\n' string of non-empty steps."""
    if isinstance(instructions, str):
        parts = instructions.split("\n")
    else:
        parts = list(instructions or [])
    return "\n".join(p.strip() for p in parts if p and p.strip())


def format_nutrients(nutrients: dict | None) -> str:
    """schema.org NutritionInformation dict -> readable multi-line string."""
    if not nutrients:
        return ""
    labels = {
        "calories": "Calories",
        "proteinContent": "Protein",
        "carbohydrateContent": "Carbs",
        "fatContent": "Fat",
        "fiberContent": "Fiber",
        "sugarContent": "Sugar",
        "sodiumContent": "Sodium",
        "servingSize": "Serving size",
    }
    lines = []
    for key, label in labels.items():
        val = nutrients.get(key)
        if val:
            lines.append(f"{label}: {val}")
    return "\n".join(lines)
