"""Sitemap discovery. Handles both <urlset> and nested <sitemapindex>."""

from __future__ import annotations

import re

from lxml import etree

_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)


def parse_sitemap(xml: str) -> tuple[list[tuple[str, str | None]], list[tuple[str, str | None]]]:
    """Return (url_entries, nested_sitemaps), each a list of (loc, lastmod).

    nested_sitemaps is populated when the document is a <sitemapindex>; its
    lastmods let the crawler visit the freshest sub-sitemaps first.
    """
    urls: list[tuple[str, str | None]] = []
    nested: list[tuple[str, str | None]] = []
    try:
        root = etree.fromstring(xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        # Fall back to a permissive regex if the XML is malformed.
        return [(loc, None) for loc in _LOC_RE.findall(xml)], []

    tag = etree.QName(root).localname.lower() if isinstance(root.tag, str) else ""
    for child in root:
        if not isinstance(child.tag, str):  # skip comments / processing instructions
            continue
        local = etree.QName(child).localname.lower()
        loc = None
        lastmod = None
        for el in child:
            if not isinstance(el.tag, str):
                continue
            name = etree.QName(el).localname.lower()
            if name == "loc":
                loc = (el.text or "").strip()
            elif name == "lastmod":
                lastmod = (el.text or "").strip()
        if not loc:
            continue
        if tag == "sitemapindex" or local == "sitemap":
            nested.append((loc, lastmod))
        else:
            urls.append((loc, lastmod))
    return urls, nested


def _matches(url: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    return re.search(pattern, url) is not None


def sitemap_entries(
    sitemap_url: str,
    url_pattern: str | None,
    http,
    *,
    max_nested: int = 25,
    limit: int | None = None,
) -> list[tuple[str, str | None]]:
    """Fetch a sitemap (recursing into indexes) and return matching (loc,
    lastmod) pairs, newest-first when <lastmod> is available.

    The lastmod is kept (not discarded) so callers can use it as a publish-date
    fallback for pages whose own JSON-LD carries no datePublished.
    """
    to_visit: list[tuple[str, str | None]] = [(sitemap_url, None)]
    visited: set[str] = set()
    collected: list[tuple[str, str | None]] = []

    while to_visit and len(visited) <= max_nested:
        current, _ = to_visit.pop(0)
        if current in visited:
            continue
        visited.add(current)
        try:
            xml = http.get(current)
        except Exception:  # noqa: BLE001 — a broken sub-sitemap shouldn't kill discovery
            continue
        entries, nested = parse_sitemap(xml)
        collected.extend(e for e in entries if _matches(e[0], url_pattern))
        # Visit freshest sub-sitemaps first so recent recipes fit under max_nested.
        to_visit.extend(nested)
        to_visit.sort(key=lambda e: e[1] or "", reverse=True)

    # Sort newest-first by lastmod (missing lastmod sorts last).
    collected.sort(key=lambda e: e[1] or "", reverse=True)
    seen: set[str] = set()
    ordered: list[tuple[str, str | None]] = []
    for loc, lastmod in collected:
        if loc not in seen:
            seen.add(loc)
            ordered.append((loc, lastmod))
    return ordered[:limit] if limit else ordered


def sitemap_urls(
    sitemap_url: str,
    url_pattern: str | None,
    http,
    *,
    max_nested: int = 25,
    limit: int | None = None,
) -> list[str]:
    """Fetch a sitemap (recursing into indexes) and return matching URLs,
    newest-first when <lastmod> is available."""
    return [
        loc
        for loc, _ in sitemap_entries(
            sitemap_url, url_pattern, http, max_nested=max_nested, limit=limit
        )
    ]
