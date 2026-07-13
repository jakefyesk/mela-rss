"""End-to-end build over fake HTTP: discover -> extract -> rehost -> feeds."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from melarss import build

from conftest import FakeHttp, load_fixture, tiny_png

FEED_URL = "https://blog.example/feed"
RECIPE_URL = "https://blog.example/recipes/newest-dish"
SITEMAP_URL = "https://rehost.example/sitemap.xml"
REHOST_RECIPE_URL = "https://rehost.example/recipes/dish"

RSS = f"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>New</title><link>{RECIPE_URL}</link></item>
<item><title>News</title><link>https://blog.example/news/moved</link></item>
</channel></rss>"""

SITEMAP = f"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>{REHOST_RECIPE_URL}</loc><lastmod>2026-06-01</lastmod></url>
<url><loc>https://rehost.example/about</loc></url>
</urlset>"""

SEED = """https://www.instagram.com/reel/ABC123/
image: https://example.com/img/finn.jpg
tags: high-protein

Creamy Peppercorn Chicken
Ingredients:
- 2 chicken breasts
- 100ml cream
Method:
1. Sear the chicken.
2. Add cream and simmer.
400cal | 42g protein
"""


class BuildFake(FakeHttp):
    ROUTES = {
        FEED_URL: RSS,
        RECIPE_URL: load_fixture("jsonld_recipe.html"),
        SITEMAP_URL: SITEMAP,
        REHOST_RECIPE_URL: load_fixture("jsonld_recipe.html"),
    }
    BINARY_ROUTES: dict[str, bytes] = {}
    DEFAULT_IMAGE = tiny_png()


def _write_sources(tmp_path: Path, seed_path: Path) -> Path:
    yaml = f"""
defaults:
  request_delay_seconds: 0
sources:
  - name: blog
    mode: link_through
    discovery: native_feed
    feed_url: "{FEED_URL}"
    url_pattern: "/recipes/"
  - name: rehostblog
    mode: rehost
    discovery: sitemap
    sitemap: "{SITEMAP_URL}"
    url_pattern: "/recipes/"
  - name: finntonry
    mode: rehost
    discovery: instagram
    seed_file: "{seed_path}"
    rehost_author: "Finn Tonry"
"""
    p = tmp_path / "sources.yaml"
    p.write_text(yaml, encoding="utf-8")
    return p


@pytest.fixture
def built(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "Http", BuildFake)
    seed = tmp_path / "seed.txt"
    seed.write_text(SEED, encoding="utf-8")
    sources = _write_sources(tmp_path, seed)
    docs = tmp_path / "docs"
    catalog = tmp_path / "data" / "catalog.json"
    summary = build.run(
        str(sources), str(docs), str(catalog), "https://host.example",
        make_bundles=True,
    )
    return docs, catalog, summary


def test_feeds_and_pages_generated(built):
    docs, catalog, summary = built
    assert (docs / ".nojekyll").exists()
    assert (docs / "feed.xml").exists()
    assert (docs / "feeds" / "blog.xml").exists()
    assert (docs / "feeds" / "rehostblog.xml").exists()
    assert (docs / "feeds" / "finntonry.xml").exists()
    assert (docs / "index.html").exists()
    assert summary["added"] == 3  # one per source


def test_linkthrough_points_at_source_page(built):
    docs, _, _ = built
    xml = (docs / "feeds" / "blog.xml").read_text()
    assert RECIPE_URL in xml  # link_through item links to the ORIGINAL page


def test_rehost_page_has_jsonld_and_selfhosted_image(built):
    docs, _, _ = built
    pages = list((docs / "recipes" / "rehostblog").glob("*.html"))
    assert pages, "rehost page should be generated"
    html = pages[0].read_text()
    assert "application/ld+json" in html
    # image was self-hosted under docs and referenced by the page
    imgs = list((docs / "recipes" / "rehostblog" / "img").glob("*.jpg"))
    assert imgs
    assert "https://host.example/recipes/rehostblog/img/" in html


def test_instagram_page_and_image(built):
    docs, _, _ = built
    pages = list((docs / "recipes" / "finntonry").glob("*.html"))
    assert pages
    html = pages[0].read_text()
    assert "Creamy Peppercorn Chicken" in html
    # confident caption -> JSON-LD emitted
    assert "application/ld+json" in html
    assert list((docs / "recipes" / "finntonry" / "img").glob("*.jpg"))


def test_bundles_written(built):
    docs, _, summary = built
    assert (docs / "bundles" / "rehostblog.melarecipes").exists()
    assert summary["bundles"]["rehostblog"] >= 1


def test_idempotent_second_run(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "Http", BuildFake)
    seed = tmp_path / "seed.txt"
    seed.write_text(SEED, encoding="utf-8")
    sources = _write_sources(tmp_path, seed)
    docs = tmp_path / "docs"
    catalog = tmp_path / "data" / "catalog.json"

    build.run(str(sources), str(docs), str(catalog), "https://host.example")
    first = json.loads(catalog.read_text())
    feed1 = (docs / "feed.xml").read_text()

    s2 = build.run(str(sources), str(docs), str(catalog), "https://host.example")
    second = json.loads(catalog.read_text())
    feed2 = (docs / "feed.xml").read_text()

    # no new recipes; identical keys; identical guids
    assert s2["added"] == 0
    assert set(first["recipes"]) == set(second["recipes"])
    guids = lambda x: re.findall(r"<guid[^>]*>(.*?)</guid>", x)
    assert guids(feed1) == guids(feed2)
