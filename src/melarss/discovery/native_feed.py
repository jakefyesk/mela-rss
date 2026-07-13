"""Discover recipe URLs from a site's existing RSS/Atom feed.

We only take the item <link>s; the recipe itself is extracted from the linked
page (link_through) so full-text vs excerpt feeds both work.
"""

from __future__ import annotations

from lxml import etree


def parse_feed_links(xml: str) -> list[str]:
    links: list[str] = []
    try:
        root = etree.fromstring(xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        return links

    for item in root.iter():
        if not isinstance(item.tag, str):  # skip comments / processing instructions
            continue
        local = etree.QName(item).localname.lower()
        if local not in ("item", "entry"):
            continue
        link = _extract_link(item)
        if link:
            links.append(link)
    return links


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


def native_feed_urls(feed_url: str, http, *, limit: int | None = None) -> list[str]:
    xml = http.get(feed_url)
    links = parse_feed_links(xml)
    return links[:limit] if limit else links
