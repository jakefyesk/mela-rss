"""Discover recipe URLs from a site's existing RSS/Atom feed.

We only take the item <link>s; the recipe itself is extracted from the linked
page (link_through) so full-text vs excerpt feeds both work. Each item's own
publish date (<pubDate>/<published>/<updated>) is kept alongside the link as a
fallback for pages whose JSON-LD omits datePublished.
"""

from __future__ import annotations

from lxml import etree

# Preference order: a true publish date beats a modification date. Dublin Core
# <dc:date> lowercases to "date".
_DATE_TAGS = ("pubdate", "published", "date", "updated")


def parse_feed_entries(xml: str) -> list[tuple[str, str | None]]:
    """Return (link, raw_date) pairs for each feed item/entry."""
    entries: list[tuple[str, str | None]] = []
    try:
        root = etree.fromstring(xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return entries

    for item in root.iter():
        if not isinstance(item.tag, str):  # skip comments / processing instructions
            continue
        local = etree.QName(item).localname.lower()
        if local not in ("item", "entry"):
            continue
        link = _extract_link(item)
        if link:
            entries.append((link, _extract_date(item)))
    return entries


def parse_feed_links(xml: str) -> list[str]:
    return [link for link, _ in parse_feed_entries(xml)]


def _extract_date(item) -> str | None:
    found: dict[str, str] = {}
    for el in item:
        if not isinstance(el.tag, str):  # skip comments / processing instructions
            continue
        name = etree.QName(el).localname.lower()
        if name in _DATE_TAGS and el.text and el.text.strip():
            found.setdefault(name, el.text.strip())
    for tag in _DATE_TAGS:
        if tag in found:
            return found[tag]
    return None


def _extract_link(item) -> str | None:
    # RSS: <link>text</link>. Atom: <link href="..." rel="alternate"/>.
    fallback = None
    for el in item:
        if not isinstance(el.tag, str):  # skip comments / processing instructions
            continue
        if etree.QName(el).localname.lower() != "link":
            continue
        href = el.get("href")
        rel = (el.get("rel") or "alternate").lower()
        if href:
            if rel == "alternate":
                return href.strip()
            fallback = fallback or href.strip()
        elif el.text and el.text.strip():
            return el.text.strip()
    return fallback


def native_feed_entries(
    feed_url: str, http, *, limit: int | None = None
) -> list[tuple[str, str | None]]:
    xml = http.get(feed_url)
    entries = parse_feed_entries(xml)
    return entries[:limit] if limit else entries


def native_feed_urls(feed_url: str, http, *, limit: int | None = None) -> list[str]:
    return [link for link, _ in native_feed_entries(feed_url, http, limit=limit)]
