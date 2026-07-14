from dataclasses import dataclass
from datetime import timezone

from melarss import normalize


@dataclass
class G:
    ingredients: list
    purpose: str | None


def test_iso8601_from_minutes():
    assert normalize.iso8601_from_minutes(20) == "PT20M"
    assert normalize.iso8601_from_minutes(95) == "PT1H35M"
    assert normalize.iso8601_from_minutes(60) == "PT1H"
    assert normalize.iso8601_from_minutes(0) == ""
    assert normalize.iso8601_from_minutes(None) == ""


def test_split_categories_removes_commas():
    tags = normalize.split_categories("Main, Dinner", "dinner", "Quick/Easy")
    assert "," not in "".join(tags)
    assert tags == ["Main", "Dinner", "Quick", "Easy"]  # case-insensitive dedup


def test_slugify_deterministic():
    assert normalize.slugify("Garlic Butter Shrimp!") == "garlic-butter-shrimp"
    assert normalize.slugify("  Éclairs & Cream  ") == "clairs-cream"
    assert normalize.slugify("") == "recipe"


def test_canonicalize_strips_tracking_and_trailing_slash():
    a = normalize.canonicalize_url("https://Example.com/recipes/x/?utm_source=ig#frag")
    b = normalize.canonicalize_url("https://example.com/recipes/x")
    assert a == b == "https://example.com/recipes/x"


def test_dedup_key_stable_and_idempotent():
    original = "https://example.com/recipes/x/?utm_source=ig"
    canonical = normalize.canonicalize_url(original)
    assert normalize.make_dedup_key("src", original) == normalize.make_dedup_key("src", canonical)
    # different source -> different key
    assert normalize.make_dedup_key("a", original) != normalize.make_dedup_key("b", original)


def test_ingredients_groups_emit_headers():
    groups = [
        G(["1 cup flour", "2 eggs"], "Batter"),
        G(["100ml cream"], "Sauce"),
    ]
    out = normalize.ingredients_to_mela(groups, ["1 cup flour", "2 eggs", "100ml cream"])
    assert out.splitlines() == ["# Batter", "1 cup flour", "2 eggs", "# Sauce", "100ml cream"]


def test_split_categories_accepts_list_from_keywords():
    # recipe-scrapers' keywords() returns a list, not a string
    tags = normalize.split_categories("Dessert", ["cookies", "easy, quick"])
    assert tags == ["Dessert", "cookies", "easy", "quick"]


def test_canonicalize_keeps_ref_like_params():
    # exact "ref" is tracking, but "reference"/"referrer" are real params
    a = normalize.canonicalize_url("https://x.com/p?reference=42")
    b = normalize.canonicalize_url("https://x.com/p?reference=99")
    assert a != b
    assert "ref=" not in normalize.canonicalize_url("https://x.com/p?ref=ig")


def test_parse_date_handles_sitemap_and_feed_shapes():
    # ISO date-only (sitemap <lastmod>)
    d = normalize.parse_date("2026-06-20")
    assert (d.year, d.month, d.day) == (2026, 6, 20)
    assert d.tzinfo is not None
    # ISO datetime with Z (Atom <updated>)
    assert normalize.parse_date("2026-06-21T12:00:00Z").tzinfo is not None
    # RFC-822 (RSS <pubDate>) -> normalized to UTC
    rfc = normalize.parse_date("Sat, 20 Jun 2026 12:00:00 GMT")
    assert rfc.astimezone(timezone.utc).hour == 12


def test_parse_date_returns_none_on_garbage():
    assert normalize.parse_date("") is None
    assert normalize.parse_date(None) is None
    assert normalize.parse_date("not a date") is None


def test_parse_date_orders_correctly():
    older = normalize.parse_date("2025-01-15")
    newer = normalize.parse_date("2026-06-20")
    assert older < newer


def test_ingredients_single_group_no_header_falls_back_flat():
    groups = [G(["1 cup flour", "2 eggs"], None)]
    out = normalize.ingredients_to_mela(groups, ["1 cup flour", "2 eggs"])
    assert out.splitlines() == ["1 cup flour", "2 eggs"]
    assert "#" not in out
