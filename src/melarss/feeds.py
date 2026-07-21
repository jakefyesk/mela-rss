"""RSS 2.0 feed generation via feedgen.

The <guid> is the recipe's dedup_key (never a mutable URL/title/date) so Mela
does not re-import on every run. The <link> is `page_url`: the source page for
link_through, our rehosted page for rehost.
"""

from __future__ import annotations

from datetime import datetime, timezone

from feedgen.feed import FeedGenerator

from .models import Recipe


def _pubdate(recipe: Recipe) -> datetime:
    dt = recipe.published_at or recipe.discovered_at or datetime(2001, 1, 1, tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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

    ordered = sorted(recipes, key=_pubdate, reverse=True)[:cap]
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
    """The capped, newest-first window actually exposed in a feed."""
    return sorted(recipes, key=_pubdate, reverse=True)[:cap]
