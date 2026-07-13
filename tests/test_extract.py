from melarss import extract
from melarss.config import SourceConfig
from melarss.models import Category, Mode

from conftest import load_fixture

CFG = SourceConfig(name="testsrc", mode=Mode.LINK_THROUGH, discovery="sitemap")


def test_extract_jsonld_recipe():
    html = load_fixture("jsonld_recipe.html")
    r = extract.extract_recipe_from_html(html, "https://example.com/recipes/shrimp", CFG)
    assert r is not None
    assert r.title == "Garlic Butter Shrimp"
    assert r.author == "Test Chef"
    assert r.total_time == "PT20M"
    assert r.prep_minutes == 10
    assert r.yield_ == "2 servings"
    assert r.image_url == "https://example.com/img/shrimp.jpg"
    # comma-containing category is split into comma-free tags
    assert "Main" in r.categories and "Dinner" in r.categories
    assert all("," not in c for c in r.categories)
    assert r.ingredients.splitlines()[0] == "1 lb shrimp"
    assert r.instructions.splitlines() == [
        "Melt butter and add garlic.",
        "Add shrimp; cook 3-4 min per side.",
    ]
    assert "Protein: 38 g" in r.nutrition
    assert r.published_at is not None and r.published_at.year == 2026


def test_extract_returns_none_for_non_recipe():
    html = load_fixture("no_recipe.html")
    r = extract.extract_recipe_from_html(html, "https://example.com/news/x", CFG)
    assert r is None


def test_og_image_fallback():
    html = load_fixture("no_recipe.html")
    assert extract.og_image(html) == "https://example.com/img/article.jpg"


def test_placeholder_data_uri_image_falls_back_to_og():
    # lazy-load sites expose a 1x1 data:image placeholder as the schema image
    html = load_fixture("jsonld_recipe.html").replace(
        '"image": ["https://example.com/img/shrimp.jpg"]',
        '"image": ["data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="]',
    )
    r = extract.extract_recipe_from_html(html, "https://example.com/recipes/shrimp", CFG)
    assert r.image_url == "https://example.com/img/fallback.jpg"  # from og:image


def test_og_image_skips_data_uri_img():
    html = '<img src="data:image/gif;base64,AAAA"><img src="https://x.com/real.jpg">'
    assert extract.og_image(html) == "https://x.com/real.jpg"


def test_extract_recipe_with_keywords_list_does_not_crash():
    # recipe-scrapers' keywords() returns a list; must not raise (would drop the
    # recipe for most food blogs, which emit a keywords field).
    html = load_fixture("jsonld_recipe.html").replace(
        '"recipeCuisine": "American",',
        '"recipeCuisine": "American",\n    "keywords": "shrimp, quick, seafood",',
    )
    r = extract.extract_recipe_from_html(html, "https://example.com/recipes/shrimp", CFG)
    assert r is not None
    assert "shrimp" in [c.lower() for c in r.categories]
    assert all("," not in c for c in r.categories)


def test_mode_and_category_propagate():
    html = load_fixture("jsonld_recipe.html")
    cfg = SourceConfig(name="s", mode=Mode.REHOST, discovery="sitemap", category=Category.COCKTAIL)
    r = extract.extract_recipe_from_html(html, "https://example.com/r/x", cfg)
    assert r.mode == Mode.REHOST
    assert r.category == Category.COCKTAIL
