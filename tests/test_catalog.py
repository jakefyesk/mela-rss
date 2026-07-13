from datetime import datetime, timezone

from melarss.catalog import Catalog
from melarss.models import Mode, Recipe


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
