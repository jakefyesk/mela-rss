import json
import re

from melarss import rehost
from melarss.models import Mode, Recipe


def make_recipe(**kw) -> Recipe:
    base = dict(
        dedup_key="k1",
        source="joshuaweissman",
        source_url="https://www.joshuaweissman.com/recipes/x",
        mode=Mode.REHOST,
        title="Test Bread",
        text="A test loaf.",
        ingredients="# Dough\n500g flour\n10g salt\n# Topping\nsesame",
        instructions="Mix.\nBake.",
        yield_="1 loaf",
        prep_time="PT20M",
        cook_time="PT40M",
        total_time="PT1H",
        categories=["Bread", "Baking"],
        image_url="https://host/img/bread.jpg",
        author="Joshua Weissman",
    )
    base.update(kw)
    return Recipe(**base)


def test_build_jsonld_shape():
    ld = rehost.build_jsonld(make_recipe())
    assert ld["@type"] == "Recipe"
    assert ld["image"] == ["https://host/img/bread.jpg"]
    assert ld["author"] == {"@type": "Person", "name": "Joshua Weissman"}
    # group headers dropped from recipeIngredient
    assert "# Dough" not in ld["recipeIngredient"]
    assert ld["recipeIngredient"][0] == "500g flour"
    # HowToStep with sequential positions
    steps = ld["recipeInstructions"]
    assert [s["position"] for s in steps] == [1, 2]
    assert all(s["@type"] == "HowToStep" for s in steps)
    # ISO-8601 durations
    assert re.match(r"^PT(\d+H)?(\d+M)?$", ld["totalTime"])
    # no None leaves
    assert None not in ld.values()


def test_render_page_contains_jsonld_and_image():
    html = rehost.render_recipe_page(make_recipe(), emit_jsonld=True)
    assert 'application/ld+json' in html
    assert 'property="og:image"' in html
    assert 'src="https://host/img/bread.jpg"' in html
    # group header rendered as a header list item, not literal '#'
    assert "Dough" in html
    # extract the JSON-LD block and confirm it parses
    block = re.search(r'application/ld\+json">\s*(\{.*?\})\s*</script>', html, re.DOTALL)
    assert block
    parsed = json.loads(block.group(1))
    assert parsed["name"] == "Test Bread"


def test_render_page_without_jsonld_for_unconfident_instagram():
    html = rehost.render_recipe_page(make_recipe(source="finntonry"), emit_jsonld=False)
    assert "application/ld+json" not in html
    # visible content + image still present so Mela's ML importer + og:image work
    assert 'property="og:image"' in html
    assert "Test Bread" in html


def test_page_relpath_stable_and_collision_free():
    r = make_recipe()
    assert rehost.page_relpath(r) == "recipes/joshuaweissman/test-bread-k1.html"
    # two recipes, same title-slug, different dedup_key -> different paths
    a = rehost.page_relpath(make_recipe(dedup_key="aaaaaaaa11", title="Same Title"))
    b = rehost.page_relpath(make_recipe(dedup_key="bbbbbbbb22", title="Same Title"))
    assert a != b


def test_autoescape_escapes_visible_fields():
    html = rehost.render_recipe_page(make_recipe(title="Fish & Chips <spicy>"), emit_jsonld=False)
    assert "<title>Fish &amp; Chips &lt;spicy&gt;</title>" in html
    assert "<spicy>" not in html  # not injected raw


def test_jsonld_script_breakout_neutralized():
    r = make_recipe(text="Sneaky </script><script>alert(1)</script>")
    html = rehost.render_recipe_page(r, emit_jsonld=True)
    # the closing tag inside JSON-LD is escaped, so there's exactly one real
    # ld+json script open and it isn't broken out of
    assert "</script><script>alert" not in html
    assert "\\u003c/script" in html
