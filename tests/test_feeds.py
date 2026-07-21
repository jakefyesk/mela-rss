from datetime import datetime, timezone

from melarss import feeds
from melarss.models import Mode, Recipe


def r(key, title, page, dt):
    return Recipe(
        dedup_key=key,
        source="s",
        source_url=f"https://src/{key}",
        mode=Mode.LINK_THROUGH,
        title=title,
        page_url=page,
        published_at=dt,
    )


RECIPES = [
    r("k1", "Old", "https://src/old", datetime(2025, 1, 1, tzinfo=timezone.utc)),
    r("k2", "New", "https://src/new", datetime(2026, 6, 1, tzinfo=timezone.utc)),
]


def test_feed_uses_dedup_key_as_guid_and_page_url_as_link():
    xml = feeds.build_feed("s", "S", "https://host/feed.xml", "https://host", RECIPES, 10).decode()
    assert "<guid isPermaLink=\"false\">k1</guid>" in xml
    assert "https://src/new" in xml
    # newest first
    assert xml.index("New") < xml.index("Old")


def test_feed_guids_stable_across_builds():
    a = feeds.build_feed("s", "S", "https://host/feed.xml", "https://host", RECIPES, 10)
    b = feeds.build_feed("s", "S", "https://host/feed.xml", "https://host", RECIPES, 10)
    import re

    guids = lambda x: re.findall(r"<guid[^>]*>(.*?)</guid>", x.decode())
    assert guids(a) == guids(b) == ["k2", "k1"]


def test_feed_cap_respected():
    many = [r(f"k{i}", f"T{i}", f"https://src/{i}", datetime(2026, 1, i + 1, tzinfo=timezone.utc)) for i in range(10)]
    xml = feeds.build_feed("s", "S", "https://host/feed.xml", "https://host", many, 3).decode()
    assert xml.count("<item>") == 3


def test_saved_via_emits_category_markers():
    forwarded = Recipe(
        dedup_key="m1",
        source="mindlink",
        source_url="https://src/m1",
        mode=Mode.REHOST,
        title="Saved One",
        page_url="https://host/recipes/mindlink/m1.html",
        saved_via="MindLink",
        categories=["tofu"],
        published_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    xml = feeds.build_feed("m", "M", "https://host/m.xml", "https://host", [forwarded], 10).decode()
    # provenance marker leads, then the user's own tags
    assert "<category>MindLink</category>" in xml
    assert "<category>tofu</category>" in xml


def test_no_category_markers_for_normal_sources():
    # a plain crawl recipe (no saved_via) must not gain <category> tags
    xml = feeds.build_feed("s", "S", "https://host/feed.xml", "https://host", RECIPES, 10).decode()
    assert "<category>" not in xml
