import json
from datetime import datetime, timedelta, timezone

from melarss.catalog import (
    Catalog,
    _iso,
    _legacy_dedup_key,
    content_fingerprint,
    record_to_recipe,
    recipe_to_record,
)
from melarss.models import Mode, Recipe
from melarss.normalize import make_dedup_key


def make(title="Dish", ingredients="a\nb", page="https://host/p"):
    return Recipe(
        dedup_key="key1",
        source="s",
        source_url="https://src/x",
        mode=Mode.REHOST,
        title=title,
        ingredients=ingredients,
        page_url=page,
    )


def test_roundtrip_serialization(tmp_path):
    cat = Catalog()
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    cat.upsert(make(), now)
    path = tmp_path / "catalog.json"
    cat.save(path, now)

    reloaded = Catalog.load(path)
    got = reloaded.get_recipe("key1")
    assert got.title == "Dish"
    assert got.page_url == "https://host/p"
    assert got.discovered_at == now


def test_saved_via_roundtrips(tmp_path):
    cat = Catalog()
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    r = make()
    r.saved_via = "MindLink"
    cat.upsert(r, now)
    path = tmp_path / "catalog.json"
    cat.save(path, now)
    got = Catalog.load(path).get_recipe("key1")
    assert got.saved_via == "MindLink"
    assert got.mela_categories()[0] == "MindLink"


def test_upsert_preserves_discovered_at_and_page_url():
    cat = Catalog()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    cat.upsert(make(page="https://host/original"), t0)

    # second run: same recipe, new object with no page_url set yet
    fresh = make(page="")
    changed = cat.upsert(fresh, t1)
    rec = cat.records["key1"]
    assert changed is False  # identical content
    assert rec["discovered_at"].startswith("2026-01-01")
    assert rec["page_url"] == "https://host/original"
    assert rec["last_seen_at"].startswith("2026-02-01")


def test_content_change_detected():
    cat = Catalog()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cat.upsert(make(ingredients="a\nb"), now)
    changed = cat.upsert(make(ingredients="a\nb\nc"), now)
    assert changed is True


def test_negative_cache_suppresses_then_expires():
    cat = Catalog()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cat.record_failure("s", "badref", "https://src/news", t0)
    # suppressed immediately after failing
    assert cat.is_suppressed("badref", t0) is True
    # still suppressed a day later (backoff >= 2 days for 1 attempt)
    assert cat.is_suppressed("badref", datetime(2026, 1, 2, tzinfo=timezone.utc)) is True
    # eventually due for retry
    assert cat.is_suppressed("badref", datetime(2027, 1, 1, tzinfo=timezone.utc)) is False


def test_negative_cache_cleared_on_success():
    cat = Catalog()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cat.record_failure("s", "ref", "https://src/x", now)
    cat.clear_failure("ref")
    assert cat.is_suppressed("ref", now) is False


def test_failures_persist_across_save_load(tmp_path):
    cat = Catalog()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cat.record_failure("s", "ref", "https://src/x", now)
    path = tmp_path / "c.json"
    cat.save(path, now)
    reloaded = Catalog.load(path)
    assert reloaded.is_suppressed("ref", now) is True


def test_mark_in_feed():
    cat = Catalog()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cat.upsert(make(), now)
    cat.mark_in_feed({"key1"})
    assert cat.records["key1"]["in_feed"] is True
    cat.mark_in_feed(set())
    assert cat.records["key1"]["in_feed"] is False


# --- duplicate guard ------------------------------------------------------

def andy(slug="teriyaki-pork", locale="", **kw):  # noqa: D401
    """A link_through recipe as the andycooks sitemap hands it to us."""
    prefix = f"/{locale}" if locale else ""
    url = f"https://www.andy-cooks.com{prefix}/blogs/recipes/{slug}"
    fields = dict(
        title="Teriyaki Pork",
        ingredients="500g pork\n2 tbsp soy",
        instructions="Sear the pork.\nGlaze and serve.",
    )
    fields.update(kw)
    return Recipe(
        dedup_key=make_dedup_key("andycooks", url),
        source="andycooks",
        source_url=url,
        mode=Mode.LINK_THROUGH,
        page_url=url,
        **fields,
    )


def test_content_fingerprint_ignores_urls_and_whitespace():
    a = andy(image_url="https://cdn/a.jpg")
    b = andy(locale="de", image_url="https://cdn/b.jpg", title="  Teriyaki   Pork ")
    b.instructions = "sear the pork.\n\nGLAZE AND SERVE."
    assert content_fingerprint(a) == content_fingerprint(b)


def test_content_fingerprint_empty_when_too_thin_to_identify():
    # no opinion rather than a collision between two contentless records
    assert content_fingerprint(andy(ingredients="", instructions="")) == ""
    assert content_fingerprint(andy(title="")) == ""
    assert content_fingerprint(andy(instructions="")) != ""  # ingredients alone is enough


def test_find_duplicate_is_scoped_to_one_source():
    cat = Catalog()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cat.upsert(andy(), now)

    moved = andy(slug="teriyaki-pork-2")           # same content, new slug
    assert cat.find_duplicate(moved) == andy().dedup_key

    other = andy()
    other.source, other.dedup_key = "jamieoliver", "other-key"
    assert cat.find_duplicate(other) is None       # two creators, two recipes


def test_alias_marks_a_ref_known_so_it_is_never_refetched():
    cat = Catalog()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cat.upsert(andy(), now)
    cat.record_alias("dupkey", "andycooks", "https://www.andy-cooks.com/x", andy().dedup_key, now)
    assert cat.has("dupkey")                       # known, so never fetched again
    assert len(cat.records) == 1                   # but not a second recipe
    assert cat.get_recipe("dupkey").dedup_key == andy().dedup_key  # resolves to the original


def test_deduplicate_merges_locale_variants_keeping_the_canonical_copy(tmp_path):
    cat = Catalog()
    early = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later = datetime(2026, 3, 1, tzinfo=timezone.utc)
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)

    # Simulate the pre-fix state: three records, one per storefront locale.
    for locale, seen in (("", later), ("de", early), ("es", later)):
        rec = recipe_to_record(andy(locale=locale))
        rec["dedup_key"] = _legacy_dedup_key("andycooks", rec["source_url"])
        rec["discovered_at"] = seen.isoformat()
        cat.records[rec["dedup_key"]] = rec
    assert len(cat.records) == 3

    stats = cat.deduplicate(now)

    assert len(cat.records) == 1
    survivor = next(iter(cat.records.values()))
    assert survivor["source_url"] == "https://www.andy-cooks.com/blogs/recipes/teriyaki-pork"
    assert survivor["dedup_key"] == make_dedup_key("andycooks", survivor["source_url"])
    # the earliest first-seen date is inherited, so ordering doesn't jump
    assert survivor["discovered_at"].startswith("2026-01-01")
    assert stats["merged"] == 2

    # idempotent: a second pass is a no-op
    before = json.loads(json.dumps(cat.records))
    assert cat.deduplicate(now) == {
        "aliased": 0, "merged": 0, "failures_rekeyed": 0, "failures_dropped": 0
    }
    assert cat.records == before


def test_deduplicate_never_renames_a_published_guid():
    """A lone locale copy keeps its key. That key is its RSS <guid>; renaming it
    would make Mela re-import a recipe it already has — the very duplication
    this whole guard exists to prevent."""
    cat = Catalog()
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    rec = recipe_to_record(andy(locale="de"))
    published_key = _legacy_dedup_key("andycooks", rec["source_url"])
    rec["dedup_key"] = published_key
    cat.records[published_key] = rec

    stats = cat.deduplicate(now)

    assert stats == {"aliased": 1, "merged": 0, "failures_rekeyed": 0, "failures_dropped": 0}
    assert list(cat.records) == [published_key]
    # the canonical URL still resolves to it, so it can't spawn a second copy
    canonical = "https://www.andy-cooks.com/blogs/recipes/teriyaki-pork"
    canonical_key = make_dedup_key("andycooks", canonical)
    assert cat.has(canonical_key)
    assert cat.resolve(canonical_key) == published_key
    assert cat.get_recipe(canonical_key).dedup_key == published_key
    # …and the canonical URL can be adopted later, still without a new guid
    assert cat.adopt_source_url(canonical_key, canonical) is True
    assert cat.records[published_key]["source_url"] == canonical
    assert list(cat.records) == [published_key]


def test_deduplicate_merges_identical_content_at_different_urls():
    cat = Catalog()
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    for slug in ("teriyaki-pork", "teriyaki-pork-recipe"):
        cat.upsert(andy(slug=slug), now)
    assert len(cat.records) == 2

    stats = cat.deduplicate(now)

    assert len(cat.records) == 1
    assert stats["merged"] == 1
    (loser, alias), = cat.aliases.items()
    survivor = next(iter(cat.records))
    assert alias["duplicate_of"] == survivor
    assert cat.has(loser)  # known, so its URL is never fetched again
    assert loser != survivor


def test_deduplicate_leaves_records_keyed_on_an_opaque_id_alone():
    # MindLink keys on its item id, not the saved URL — rewriting that key from
    # source_url would change the <guid> of every saved recipe.
    cat = Catalog()
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    saved = Recipe(
        dedup_key=make_dedup_key("mindlink", "mindlink://item/abc-123"),
        source="mindlink",
        source_url="https://www.instagram.com/de/p/XYZ",
        mode=Mode.REHOST,
        title="Saved One",
        ingredients="a\nb",
        instructions="mix",
    )
    cat.upsert(saved, now)

    cat.deduplicate(now)

    assert list(cat.records) == [saved.dedup_key]


def test_deduplicate_realigns_the_negative_cache():
    cat = Catalog()
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    url = "https://www.andy-cooks.com/es/products/apron"
    stale = _legacy_dedup_key("andycooks", url)
    cat.failures[stale] = {
        "source": "andycooks", "source_url": url, "attempts": 2,
        "last_attempt": _iso(now), "next_retry": _iso(now + timedelta(days=4)),
    }

    stats = cat.deduplicate(now)

    assert stats["failures_rekeyed"] == 1
    live = make_dedup_key("andycooks", url)
    assert live in cat.failures and stale not in cat.failures
    assert cat.is_suppressed(live, now)  # backoff survived the re-key


def test_merge_takes_every_missing_field_from_the_copy():
    cat = Catalog()
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    thin = recipe_to_record(andy(notes="", nutrition="", image_url=""))
    thin["dedup_key"] = make_dedup_key("andycooks", thin["source_url"])
    rich = recipe_to_record(
        andy(locale="de", notes="rest 10 min", nutrition="Calories: 500", image_url="https://cdn/a.jpg")
    )
    rich["dedup_key"] = _legacy_dedup_key("andycooks", rich["source_url"])
    cat.records = {thin["dedup_key"]: thin, rich["dedup_key"]: rich}

    cat.deduplicate(now)

    (survivor,) = cat.records.values()
    assert survivor["notes"] == "rest 10 min"
    assert survivor["nutrition"] == "Calories: 500"
    assert survivor["image_url"] == "https://cdn/a.jpg"
    # identity is never taken from the copy
    assert survivor["source_url"] == "https://www.andy-cooks.com/blogs/recipes/teriyaki-pork"
    # digests describe the record as it is now, not as it was before the merge
    assert survivor["content_hash"] == recipe_to_record(record_to_recipe(survivor))["content_hash"]


def test_adopt_source_url_upgrades_a_locale_url_but_never_repoints():
    cat = Catalog()
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    cat.upsert(andy(locale="de"), now)
    key = make_dedup_key("andycooks", "https://www.andy-cooks.com/blogs/recipes/teriyaki-pork")
    canonical = "https://www.andy-cooks.com/blogs/recipes/teriyaki-pork"

    key = cat.resolve(key)
    assert cat.adopt_source_url(key, canonical) is True
    assert cat.records[key]["source_url"] == canonical
    assert cat.records[key]["page_url"] == canonical
    assert cat.records[key]["dedup_key"] == key  # guid untouched

    # a URL belonging to a different recipe is refused
    assert cat.adopt_source_url(key, "https://www.andy-cooks.com/blogs/recipes/other") is False
    assert cat.records[key]["source_url"] == canonical
