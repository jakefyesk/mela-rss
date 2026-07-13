"""Optional: enumerate a Mob chef's recipes (the reliable finntonry backbone).

Mob is a modern JS site behind Cloudflare, so enumeration is best-effort:
1. parse recipe anchors straight from the server-rendered HTML, and
2. mine any Next.js __NEXT_DATA__ JSON blob for recipe slugs.
Extraction then goes through the normal link_through path (mob has clean JSON-LD).
"""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

_RECIPE_HREF = re.compile(r"^/recipes/[a-z0-9-]+/?$", re.IGNORECASE)


def _abs(base: str, href: str) -> str:
    if href.startswith("http"):
        return href
    root = re.match(r"^(https?://[^/]+)", base)
    origin = root.group(1) if root else "https://www.mob.co.uk"
    return origin + href


def parse_chef_page(html: str, base_url: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if _RECIPE_HREF.match(href):
            full = _abs(base_url, href)
            if full not in seen:
                seen.add(full)
                urls.append(full)

    # Next.js data blob: harvest any "/recipes/<slug>" strings.
    blob = soup.find("script", id="__NEXT_DATA__")
    if blob and blob.string:
        try:
            data = json.dumps(json.loads(blob.string))
            for slug in re.findall(r"/recipes/[a-z0-9-]+", data, re.IGNORECASE):
                full = _abs(base_url, slug)
                if full not in seen:
                    seen.add(full)
                    urls.append(full)
        except (json.JSONDecodeError, TypeError):
            pass
    return urls


def mob_chef_recipe_urls(chef_url: str, http, *, limit: int | None = None) -> list[str]:
    html = http.get(chef_url)
    urls = parse_chef_page(html, chef_url)
    return urls[:limit] if limit else urls
