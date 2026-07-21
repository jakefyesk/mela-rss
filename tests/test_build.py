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


class SuppressFake(FakeHttp):
    ROUTES = {
        "https://blog.example/feed2": (
            '<?xml version="1.0"?><rss version="2.0"><channel>'
            f"<item><link>{RECIPE_URL}</link></item>"
            "<item><link>https://blog.example/news/moved</link></item>"
            "</channel></rss>"
        ),
        RECIPE_URL: load_fixture("jsonld_recipe.html"),
        "https://blog.example/news/moved": load_fixture("no_recipe.html"),
    }
    BINARY_ROUTES: dict[str, bytes] = {}
    DEFAULT_IMAGE = tiny_png()


def test_non_recipe_ref_is_suppressed(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "Http", SuppressFake)
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        'defaults: {request_delay_seconds: 0}\n'
        'sources:\n'
        '  - name: blog\n'
        '    mode: link_through\n'
        '    discovery: native_feed\n'
        '    feed_url: "https://blog.example/feed2"\n',
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    catalog = tmp_path / "data" / "catalog.json"

    s1 = build.run(str(sources), str(docs), str(catalog), "https://host.example")
    cat = json.loads(catalog.read_text())
    # one real recipe cataloged; the non-recipe link recorded as a failure
    assert s1["added"] == 1
    assert len(cat["recipes"]) == 1
    assert len(cat["failures"]) == 1

    # second run: recipe known, bad ref suppressed -> nothing reprocessed
    s2 = build.run(str(sources), str(docs), str(catalog), "https://host.example")
    assert s2["added"] == 0


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


# --- publish-date fallback from discovery ---------------------------------

NODATE_URL = "https://blog.example/recipes/dateless-dish"
RSS_WITH_PUBDATE = (
    '<?xml version="1.0"?><rss version="2.0"><channel>'
    f"<item><title>Dateless</title><link>{NODATE_URL}</link>"
    "<pubDate>Sat, 20 Jun 2026 12:00:00 GMT</pubDate></item>"
    "</channel></rss>"
)


class DateFallbackFake(FakeHttp):
    ROUTES = {
        "https://blog.example/feed3": RSS_WITH_PUBDATE,
        NODATE_URL: load_fixture("jsonld_recipe_nodate.html"),
    }
    BINARY_ROUTES: dict[str, bytes] = {}
    DEFAULT_IMAGE = tiny_png()


def test_published_at_falls_back_to_feed_pubdate(tmp_path, monkeypatch):
    # The page has no datePublished, but the feed item carries a <pubDate>; the
    # recipe should still land in the catalog dated, not undated.
    monkeypatch.setattr(build, "Http", DateFallbackFake)
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "defaults: {request_delay_seconds: 0}\n"
        "sources:\n"
        "  - name: blog\n"
        "    mode: link_through\n"
        "    discovery: native_feed\n"
        '    feed_url: "https://blog.example/feed3"\n'
        '    url_pattern: "/recipes/"\n',
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    catalog_path = tmp_path / "data" / "catalog.json"
    build.run(str(sources), str(docs), str(catalog_path), "https://host.example")

    rec = next(iter(json.loads(catalog_path.read_text())["recipes"].values()))
    assert rec["published_at"] is not None
    assert rec["published_at"].startswith("2026-06-20")
    # and the RSS <pubDate> reflects it (Mela orders the feed by this)
    assert "2026" in (docs / "feed.xml").read_text()


BACKFILL_URL = "https://sm.example/recipes/known-dish"
BACKFILL_SITEMAP = (
    '<?xml version="1.0"?>'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    f"<url><loc>{BACKFILL_URL}</loc><lastmod>2026-03-15</lastmod></url>"
    "</urlset>"
)


class BackfillFake(FakeHttp):
    ROUTES = {
        "https://sm.example/sitemap.xml": BACKFILL_SITEMAP,
        BACKFILL_URL: load_fixture("jsonld_recipe_nodate.html"),
    }
    BINARY_ROUTES: dict[str, bytes] = {}
    DEFAULT_IMAGE = tiny_png()


def test_backfill_dates_existing_dateless_record_without_refetch(tmp_path, monkeypatch):
    # A recipe imported before we captured dates (published_at=None) should get
    # its date backfilled from the sitemap <lastmod> on the next run — even
    # though it's already known and never re-fetched.
    from datetime import datetime, timezone

    from melarss.catalog import Catalog
    from melarss.models import Mode, Recipe
    from melarss.normalize import make_dedup_key

    catalog_path = tmp_path / "data" / "catalog.json"
    docs = tmp_path / "docs"
    seeded_at = datetime(2026, 7, 1, tzinfo=timezone.utc)

    cat = Catalog({})
    key = make_dedup_key("smblog", BACKFILL_URL)
    cat.upsert(
        Recipe(
            dedup_key=key,
            source="smblog",
            source_url=BACKFILL_URL,
            mode=Mode.LINK_THROUGH,
            title="Known Dish",
            ingredients="1 cup flour",
            instructions="Mix.",
            page_url=BACKFILL_URL,
            published_at=None,
        ),
        seeded_at,
    )
    cat.save(catalog_path, seeded_at)
    assert json.loads(catalog_path.read_text())["recipes"][key]["published_at"] is None

    monkeypatch.setattr(build, "Http", BackfillFake)
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "defaults: {request_delay_seconds: 0}\n"
        "sources:\n"
        "  - name: smblog\n"
        "    mode: link_through\n"
        "    discovery: sitemap\n"
        '    sitemap: "https://sm.example/sitemap.xml"\n'
        '    url_pattern: "/recipes/"\n',
        encoding="utf-8",
    )
    s = build.run(str(sources), str(docs), str(catalog_path), "https://host.example")

    # already known -> nothing added, but its date got backfilled
    assert s["added"] == 0
    rec = json.loads(catalog_path.read_text())["recipes"][key]
    assert rec["published_at"].startswith("2026-03-15")


# --- MindLink: forwarded recipes land tagged + identifiable ----------------

ML_BASE = "https://mind-link.example"
ML_LIST = f"{ML_BASE}/api/v1/items?type=recipe&limit=50"
ML_DETAIL = f"{ML_BASE}/api/v1/items/ml-1"
ML_ITEM = {
    "id": "ml-1",
    "title": "Gochujang Noodles",
    "url": "https://www.instagram.com/p/ABC/",
    "tags": ["noodles"],
    "created_at": "2026-07-18T00:00:00Z",
    "enriched_at": "2026-07-18T00:05:00Z",
}
ML_DETAIL_BODY = dict(
    ML_ITEM,
    summary="Fast spicy noodles.",
    ocr_text="Gochujang Noodles\nIngredients:\n- 200g noodles\n- 2 tbsp gochujang\nMethod:\n1. Boil noodles.\n2. Toss in sauce.\n",
    metadata={"author": "@chef"},
    media=[{"kind": "thumbnail", "mime_type": "image/webp", "url": "https://storage/signed/ml.webp"}],
)


class MindLinkBuildFake(FakeHttp):
    ROUTES = {
        ML_LIST: json.dumps({"items": [ML_ITEM], "next_cursor": None}),
        ML_DETAIL: json.dumps(ML_DETAIL_BODY),
    }
    BINARY_ROUTES: dict[str, bytes] = {}
    DEFAULT_IMAGE = tiny_png()


def test_mindlink_source_ingests_tagged_and_identifiable(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "Http", MindLinkBuildFake)
    monkeypatch.setenv("MINDLINK_API_URL", ML_BASE)
    monkeypatch.setenv("MINDLINK_TOKEN", "mlk_test")
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "defaults: {request_delay_seconds: 0}\n"
        "sources:\n"
        "  - name: mindlink\n"
        "    mode: rehost\n"
        "    discovery: mindlink\n"
        '    display_name: "MindLink"\n',
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    catalog = tmp_path / "data" / "catalog.json"
    summary = build.run(str(sources), str(docs), str(catalog), "https://host.example")

    assert summary["added"] == 1
    # rehosted page carries the badge + JSON-LD with the MindLink category
    pages = list((docs / "recipes" / "mindlink").glob("*.html"))
    assert pages
    html = pages[0].read_text()
    assert "Saved via MindLink" in html
    assert "application/ld+json" in html
    assert "MindLink" in html
    # the recipe feed marks the item as saved via MindLink
    recipes_xml = (docs / "recipes.xml").read_text()
    assert "Gochujang Noodles" in recipes_xml
    assert "<category>MindLink</category>" in recipes_xml


ML_UNCONFIDENT_ITEM = {"id": "ml-2", "title": "Mystery Dish", "created_at": "2026-07-18T00:00:00Z"}
ML_UNCONFIDENT_DETAIL = dict(
    ML_UNCONFIDENT_ITEM,
    summary="Looks tasty.",
    ocr_text="just a vibe, no recipe structure here",  # -> caption parse unconfident
    media=[{"kind": "thumbnail", "mime_type": "image/webp", "url": "https://storage/signed/ml2.webp"}],
)


class MindLinkUnconfidentFake(FakeHttp):
    ROUTES = {
        ML_LIST: json.dumps({"items": [ML_UNCONFIDENT_ITEM], "next_cursor": None}),
        f"{ML_BASE}/api/v1/items/ml-2": json.dumps(ML_UNCONFIDENT_DETAIL),
    }
    BINARY_ROUTES: dict[str, bytes] = {}
    DEFAULT_IMAGE = tiny_png()


def test_mindlink_unconfident_recipe_still_carries_marker(tmp_path, monkeypatch):
    # Even when caption heuristics can't extract a recipe, the built page must
    # emit a marker JSON-LD so "MindLink" reaches Mela — but without unreliable
    # ingredients/steps.
    monkeypatch.setattr(build, "Http", MindLinkUnconfidentFake)
    monkeypatch.setenv("MINDLINK_API_URL", ML_BASE)
    monkeypatch.setenv("MINDLINK_TOKEN", "mlk_test")
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "defaults: {request_delay_seconds: 0}\n"
        "sources:\n  - name: mindlink\n    mode: rehost\n    discovery: mindlink\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    build.run(str(sources), str(docs), str(tmp_path / "data" / "catalog.json"), "https://host.example")
    html = list((docs / "recipes" / "mindlink").glob("*.html"))[0].read_text()
    assert "application/ld+json" in html
    assert '"recipeCategory"' in html
    assert "MindLink" in html
    assert "recipeIngredient" not in html  # unreliable structure withheld


class MindLinkNoImageFake(FakeHttp):
    ROUTES = {
        ML_LIST: json.dumps({"items": [ML_ITEM], "next_cursor": None}),
        ML_DETAIL: json.dumps(ML_DETAIL_BODY),
    }
    BINARY_ROUTES: dict[str, bytes] = {}
    DEFAULT_IMAGE = None  # every image download fails


def test_mindlink_image_failure_does_not_persist_expiring_url(tmp_path, monkeypatch):
    # A signed Storage URL that fails to self-host must NOT be baked into the
    # catalog/feed (it would expire); image_url is dropped instead.
    monkeypatch.setattr(build, "Http", MindLinkNoImageFake)
    monkeypatch.setenv("MINDLINK_API_URL", ML_BASE)
    monkeypatch.setenv("MINDLINK_TOKEN", "mlk_test")
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "defaults: {request_delay_seconds: 0}\n"
        "sources:\n  - name: mindlink\n    mode: rehost\n    discovery: mindlink\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    catalog = tmp_path / "data" / "catalog.json"
    build.run(str(sources), str(docs), str(catalog), "https://host.example")
    rec = next(iter(json.loads(catalog.read_text())["recipes"].values()))
    assert rec["image_url"] == ""  # expiring signed URL not persisted
    assert "storage/signed" not in (docs / "recipes.xml").read_text()


def test_mindlink_source_unconfigured_is_skipped(tmp_path, monkeypatch):
    # No token/url in the environment -> the source is a graceful no-op and the
    # build still completes, writing a valid (empty) recipes feed.
    monkeypatch.setattr(build, "Http", MindLinkBuildFake)
    monkeypatch.delenv("MINDLINK_API_URL", raising=False)
    monkeypatch.delenv("MINDLINK_TOKEN", raising=False)
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "defaults: {request_delay_seconds: 0}\n"
        "sources:\n"
        "  - name: mindlink\n"
        "    mode: rehost\n"
        "    discovery: mindlink\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    catalog = tmp_path / "data" / "catalog.json"
    summary = build.run(str(sources), str(docs), str(catalog), "https://host.example")
    assert summary["added"] == 0
    assert (docs / "recipes.xml").exists()


# --- category feeds: recipes vs cocktails stay separate --------------------

CAT_FEED_URL = "https://cook.example/feed"
CAT_RECIPE_URL = "https://cook.example/recipes/garlic-shrimp"

COCKTAIL_SEED = """https://www.instagram.com/reel/NEGRONI1/
image: https://example.com/img/negroni.jpg
tags: cocktail

Smoked Negroni
Ingredients:
- 1 oz gin
- 1 oz Campari
- 1 oz sweet vermouth
Method:
1. Stir with ice until chilled.
2. Strain over a large cube.
3. Express an orange peel over the top.
"""


class CategoryFake(FakeHttp):
    ROUTES = {
        CAT_FEED_URL: (
            '<?xml version="1.0"?><rss version="2.0"><channel>'
            f"<item><title>Shrimp</title><link>{CAT_RECIPE_URL}</link></item>"
            "</channel></rss>"
        ),
        CAT_RECIPE_URL: load_fixture("jsonld_recipe.html"),
    }
    BINARY_ROUTES: dict[str, bytes] = {}
    DEFAULT_IMAGE = tiny_png()


@pytest.fixture
def category_built(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "Http", CategoryFake)
    seed = tmp_path / "cocktails.txt"
    seed.write_text(COCKTAIL_SEED, encoding="utf-8")
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "defaults:\n"
        "  request_delay_seconds: 0\n"
        "sources:\n"
        "  - name: cook\n"
        "    mode: link_through\n"
        "    discovery: native_feed\n"
        f'    feed_url: "{CAT_FEED_URL}"\n'
        '    url_pattern: "/recipes/"\n'
        "  - name: barcart\n"
        "    mode: rehost\n"
        "    discovery: instagram\n"
        "    category: cocktail\n"
        "    display_name: Bar Cart\n"
        f'    seed_file: "{seed}"\n'
        '    rehost_author: "Bar Cart"\n',
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    catalog = tmp_path / "data" / "catalog.json"
    build.run(str(sources), str(docs), str(catalog), "https://host.example")
    return docs


def test_category_feeds_written(category_built):
    docs = category_built
    assert (docs / "recipes.xml").exists()
    assert (docs / "cocktails.xml").exists()
    assert (docs / "feed.xml").exists()  # combined firehose still emitted


def test_recipes_and_cocktails_do_not_cross_contaminate(category_built):
    docs = category_built
    recipes_xml = (docs / "recipes.xml").read_text()
    cocktails_xml = (docs / "cocktails.xml").read_text()

    # cocktail lands only in the cocktail feed
    assert "Smoked Negroni" in cocktails_xml
    assert "Smoked Negroni" not in recipes_xml
    # recipe lands only in the recipe feed
    assert "Garlic Butter Shrimp" in recipes_xml
    assert "Garlic Butter Shrimp" not in cocktails_xml


def test_combined_feed_carries_both_categories(category_built):
    feed_xml = (category_built / "feed.xml").read_text()
    assert "Smoked Negroni" in feed_xml
    assert "Garlic Butter Shrimp" in feed_xml


def test_index_lists_both_category_subscribe_urls(category_built):
    html = (category_built / "index.html").read_text()
    assert "https://host.example/recipes.xml" in html
    assert "https://host.example/cocktails.xml" in html
    assert "Bar Cart" in html  # display_name override surfaces on the index


def test_empty_category_feed_is_still_written(tmp_path, monkeypatch):
    # A configured cocktail source with no importable posts yet must still yield a
    # valid (empty) cocktails.xml so the Mela subscribe URL never 404s.
    monkeypatch.setattr(build, "Http", CategoryFake)
    empty_seed = tmp_path / "empty.txt"
    empty_seed.write_text("# no posts yet\n", encoding="utf-8")
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "defaults:\n"
        "  request_delay_seconds: 0\n"
        "sources:\n"
        "  - name: cook\n"
        "    mode: link_through\n"
        "    discovery: native_feed\n"
        f'    feed_url: "{CAT_FEED_URL}"\n'
        '    url_pattern: "/recipes/"\n'
        "  - name: barcart\n"
        "    mode: rehost\n"
        "    discovery: instagram\n"
        "    category: cocktail\n"
        f'    seed_file: "{empty_seed}"\n',
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    catalog = tmp_path / "data" / "catalog.json"
    build.run(str(sources), str(docs), str(catalog), "https://host.example")

    cocktails_xml = (docs / "cocktails.xml").read_text()
    assert "<rss" in cocktails_xml  # valid RSS, just no <item>s
    assert "<item>" not in cocktails_xml
