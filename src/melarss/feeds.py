"""RSS 2.0 feed generation via feedgen.

The <guid> is the recipe's dedup_key (never a mutable URL/title/date) so Mela
does not re-import on every run. The <link> is `page_url`: the source page for
link_through, our rehosted page for rehost.

`selected_for_feed` is the single place a feed's window is chosen, and it is the
last line of defence against duplication: whatever state the catalog is in, one
recipe is published once.
"""

from __future__ import annotations

from datetime import datetime, timezone

from feedgen.feed import FeedGenerator

from .catalog import content_fingerprint
from .models import Recipe

_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def _pubdate(recipe: Recipe) -> datetime:
    dt = recipe.published_at or recipe.discovered_at or _EPOCH
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _first_seen(recipe: Recipe) -> datetime:
    dt = recipe.discovered_at or _EPOCH
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _summary(recipe: Recipe) -> str:
    bits = []
    if recipe.text:
        bits.append(recipe.text)
    meta = []
    if recipe.yield_:
        meta.append(recipe.yield_)
    if recipe.total_time:
        meta.append(recipe.total_time)
    if meta:
        bits.append(" · ".join(meta))
    return " — ".join(bits) if bits else recipe.title


def build_feed(
    name: str,
    title: str,
    self_url: str,
    site_url: str,
    recipes: list[Recipe],
    cap: int,
) -> bytes:
    fg = FeedGenerator()
    fg.id(self_url)
    fg.title(title)
    fg.link(href=self_url, rel="self")
    fg.link(href=site_url, rel="alternate")
    fg.description(f"Auto-curated recipes from {title}, ready to import into Mela.")
    fg.language("en")
    fg.generator("mela-rss")

    ordered = selected_for_feed(recipes, cap)
    # feedgen prepends entries, so add oldest-first to end up newest-first.
    for recipe in reversed(ordered):
        fe = fg.add_entry()
        fe.id(recipe.dedup_key)
        fe.guid(recipe.dedup_key, permalink=False)
        fe.title(recipe.title or "Untitled recipe")
        fe.link(href=recipe.page_url or recipe.source_url)
        fe.description(_summary(recipe))
        fe.pubDate(_pubdate(recipe))
        if recipe.author:
            fe.author(name=recipe.author)
        # Provenance marker: forwarded recipes (e.g. saved via MindLink) carry
        # <category> tags so they're identifiable straight from the raw feed.
        if recipe.saved_via:
            for term in recipe.mela_categories():
                fe.category(term=term)

    return fg.rss_str(pretty=True)


def selected_for_feed(recipes: list[Recipe], cap: int) -> list[Recipe]:
    """The capped, newest-first window actually exposed in a feed.

    Collapses recipes from one source whose content is identical before capping,
    so a duplicate that somehow reached the catalog still can't reach the reader
    — and, just as importantly, can't push a real recipe out of the window.
    Duplicates *across* sources are left alone: two creators publishing the same
    dish are two recipes, and silently dropping one would hide a source.

    Ordering is total (publish date, then first-seen, then key) so a rebuild of
    unchanged state always emits an identical feed.
    """
    ordered = sorted(recipes, key=lambda r: (_first_seen(r), r.dedup_key))
    ordered.sort(key=_pubdate, reverse=True)

    out: list[Recipe] = []
    seen: set[tuple[str, str]] = set()
    for recipe in ordered:
        if len(out) >= cap:
            break
        fingerprint = content_fingerprint(recipe)
        if fingerprint:
            identity = (recipe.source, fingerprint)
            if identity in seen:
                continue
            seen.add(identity)
        out.append(recipe)
    return out
