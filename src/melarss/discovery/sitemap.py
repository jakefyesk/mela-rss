"""Sitemap discovery. Handles both <urlset> and nested <sitemapindex>."""

from __future__ import annotations

import re

from lxml import etree

_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)


def parse_sitemap(xml: str) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Return (url_entries, nested_sitemap_urls).

    url_entries is a list of (loc, lastmod). nested_sitemap_urls is populated
    when the document is a <sitemapindex>.
    """
    urls: list[tuple[str, str | None]] = []
    nested: list[str] = []
    try:
        root = etree.fromstring(xml.encode("utf-8"))
    except etree.XMLSyntaxError:
        # Fall back to a permissive regex if the XML is malformed.
        return [(loc, None) for loc in _LOC_RE.findall(xml)], []

    tag = etree.QName(root).localname.lower() if root.tag else ""
    for child in root:
        local = etree.QName(child).localname.lower()
        loc = None
        lastmod = None
        for el in child:
            name = etree.QName(el).localname.lower()
            if name == "loc":
                loc = (el.text or "").strip()
            elif name == "lastmod":
                lastmod = (el.text or "").strip()
        if not loc:
            continue
        if tag == "sitemapindex" or local == "sitemap":
            nested.append(loc)
        else:
            urls.append((loc, lastmod))
    return urls, nested


def _matches(url: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    return re.search(pattern, url) is not None


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
    to_visit = [sitemap_url]
    visited: set[str] = set()
    collected: list[tuple[str, str | None]] = []

    while to_visit and len(visited) <= max_nested:
        current = to_visit.pop(0)
        if current in visited:
            continue
        visited.add(current)
        try:
            xml = http.get(current)
        except Exception:  # noqa: BLE001 — a broken sub-sitemap shouldn't kill discovery
            continue
        entries, nested = parse_sitemap(xml)
        collected.extend(e for e in entries if _matches(e[0], url_pattern))
        to_visit.extend(nested)

    # Sort newest-first by lastmod (missing lastmod sorts last).
    collected.sort(key=lambda e: e[1] or "", reverse=True)
    seen: set[str] = set()
    ordered: list[str] = []
    for loc, _ in collected:
        if loc not in seen:
            seen.add(loc)
            ordered.append(loc)
    return ordered[:limit] if limit else ordered
