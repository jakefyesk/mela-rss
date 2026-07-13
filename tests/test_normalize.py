from dataclasses import dataclass

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


def test_ingredients_single_group_no_header_falls_back_flat():
    groups = [G(["1 cup flour", "2 eggs"], None)]
    out = normalize.ingredients_to_mela(groups, ["1 cup flour", "2 eggs"])
    assert out.splitlines() == ["1 cup flour", "2 eggs"]
    assert "#" not in out
