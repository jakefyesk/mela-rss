"""Discover recipe URLs by scraping an HTML index/listing page.

Some sites (Joshua Weissman, Mob) are custom/SPA platforms whose sitemap doesn't
list individual recipes. Their listing pages do link to them, though — either as
server-rendered <a href> anchors or as paths embedded in a Next.js/Squarespace
JSON blob. We harvest both and filter by the source's url_pattern.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

_JSON_PATH_RE = re.compile(r'"(/[A-Za-z0-9][^"\\\s]*)"')


def _origin(url: str) -> str:
    m = re.match(r"^(https?://[^/]+)", url)
    return m.group(1) if m else ""


def _absolutize(base_url: str, href: str) -> str:
    href = href.split("#")[0].split("?")[0]
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = _origin(base_url) + href
    elif not href.startswith("http"):
        return href
    # normalize a trailing slash (keep the root) so URLs match the pattern and
    # dedupe consistently
    if href.endswith("/") and href.count("/") > 3:
        href = href.rstrip("/")
    return href


def extract_index_links(html: str, base_url: str, url_pattern: str | None) -> list[str]:
    rx = re.compile(url_pattern) if url_pattern else None
    urls: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        if not candidate:
            return
        full = _absolutize(base_url, candidate)
        if not full.startswith("http"):
            return
        if rx and not rx.search(full):
            return
        if full not in seen:
            seen.add(full)
            urls.append(full)

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        add(a["href"])
    # Embedded JSON (Next.js __NEXT_DATA__, Squarespace static data, etc.).
    for script in soup.find_all("script"):
        text = script.string or ""
        if "/" not in text:
            continue
        for path in _JSON_PATH_RE.findall(text):
            add(path)
    return urls


def index_page_urls(
    index_url: str,
    url_pattern: str | None,
    http,
    *,
    extra_index_urls: tuple[str, ...] = (),
    limit: int | None = None,
) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for page in (index_url, *extra_index_urls):
        try:
            html = http.get(page)
        except Exception:  # noqa: BLE001 — one dead index page shouldn't kill discovery
            continue
        for u in extract_index_links(html, page, url_pattern):
            if u not in seen:
                seen.add(u)
                urls.append(u)
    return urls[:limit] if limit else urls
