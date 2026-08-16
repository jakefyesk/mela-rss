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


def _dup_pair():
    """Two records holding the same recipe — the shape a localized sitemap and
    a moved slug both produce."""
    body = dict(
        title="Teriyaki Pork",
        ingredients="500g pork\n2 tbsp soy",
        instructions="Sear the pork.",
        published_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    keep = Recipe(
        dedup_key="keep", source="andycooks", mode=Mode.LINK_THROUGH,
        source_url="https://andy/blogs/recipes/x", page_url="https://andy/blogs/recipes/x",
        discovered_at=datetime(2026, 1, 1, tzinfo=timezone.utc), **body,
    )
    copy = Recipe(
        dedup_key="copy", source="andycooks", mode=Mode.LINK_THROUGH,
        source_url="https://andy/de/blogs/recipes/x", page_url="https://andy/de/blogs/recipes/x",
        discovered_at=datetime(2026, 2, 1, tzinfo=timezone.utc), **body,
    )
    return keep, copy


def test_feed_publishes_one_recipe_once():
    keep, copy = _dup_pair()
    xml = feeds.build_feed("a", "A", "https://host/a.xml", "https://host", [copy, keep], 10).decode()
    assert xml.count("<item>") == 1
    assert "<guid isPermaLink=\"false\">keep</guid>" in xml  # the first-seen copy wins


def test_duplicates_do_not_consume_feed_slots():
    keep, copy = _dup_pair()
    other = r("k9", "Something Else", "https://src/9", datetime(2026, 5, 1, tzinfo=timezone.utc))
    other.ingredients, other.instructions = "flour", "bake"
    # cap of 2: without collapsing, the duplicate would push `other` out
    selected = feeds.selected_for_feed([keep, copy, other], 2)
    assert [x.dedup_key for x in selected] == ["keep", "k9"]


def test_identical_content_from_two_sources_is_kept():
    keep, copy = _dup_pair()
    copy.source, copy.dedup_key = "jamieoliver", "jamie"
    selected = feeds.selected_for_feed([keep, copy], 10)
    assert {x.dedup_key for x in selected} == {"keep", "jamie"}


def test_feed_order_is_total_when_publish_dates_tie():
    same = datetime(2026, 6, 1, tzinfo=timezone.utc)
    a = r("bbb", "B", "https://src/b", same)
    b = r("aaa", "A", "https://src/a", same)
    assert [x.dedup_key for x in feeds.selected_for_feed([a, b], 10)] == ["aaa", "bbb"]
    assert [x.dedup_key for x in feeds.selected_for_feed([b, a], 10)] == ["aaa", "bbb"]
