from melarss.caption import parse_caption

CAPTION = """Creamy Peppercorn Chicken 🔥 Recipe in caption ⬇️

Ingredients:
- 2 chicken breasts
- 1 tbsp crushed black peppercorns
- 100ml double cream

Method:
1. Season and sear the chicken until golden.
2. Add peppercorns and cream, simmer to thicken.
3. Return chicken and coat in the sauce.

400cal | 42g protein
Serves 2

#highprotein #chicken #dinner
"""


def test_parse_caption_structured():
    p = parse_caption(CAPTION)
    assert p.title == "Creamy Peppercorn Chicken"
    assert p.ingredients == [
        "2 chicken breasts",
        "1 tbsp crushed black peppercorns",
        "100ml double cream",
    ]
    assert p.instructions[0].startswith("Season and sear")
    assert len(p.instructions) == 3
    assert "protein" in p.nutrition.lower()
    assert "2" in p.yield_
    assert p.confident is True


def test_parse_caption_unstructured_not_confident():
    p = parse_caption("Just a vibe today, no recipe here. #foodie")
    assert p.confident is False
    # still yields a usable title so the page isn't blank
    assert p.title


def test_hashtags_excluded_from_body():
    p = parse_caption(CAPTION)
    assert "#highprotein" not in " ".join(p.ingredients + p.instructions)


def test_metric_ingredient_not_stolen_by_macro_detector():
    cap = "Ingredients:\n200g flour\n100g sugar\n2 eggs\nMethod:\nMix and bake"
    p = parse_caption(cap)
    assert p.ingredients == ["200g flour", "100g sugar", "2 eggs"]


def test_prose_for_the_line_is_not_an_ingredient_header():
    cap = "Roast Chicken\nFor the best results, salt the bird overnight.\nIngredients:\n1 chicken\nMethod:\nRoast it"
    p = parse_caption(cap)
    assert p.ingredients == ["1 chicken"]
    assert not any(i.startswith("#") for i in p.ingredients)


def test_for_the_colon_is_a_group_header():
    cap = "Pasta\nFor the sauce:\n2 tomatoes\n1 clove garlic\nMethod:\nSimmer"
    p = parse_caption(cap)
    assert p.ingredients[0] == "# For the sauce"
    assert "2 tomatoes" in p.ingredients


def test_serve_instruction_not_eaten_as_yield():
    cap = (
        "Steak\nIngredients:\n1 steak\nMethod:\n"
        "Sear the steak, then serve to 4 guests with a generous pour of sauce"
    )
    p = parse_caption(cap)
    assert any("serve to 4 guests" in s.lower() for s in p.instructions)


def test_title_recipe_in_caption_stripped_but_real_title_kept():
    assert parse_caption("Best Cookies — recipe in bio\nIngredients:\nflour").title == "Best Cookies"
    assert parse_caption("A recipe in 20 minutes\nIngredients:\nflour").title == "A recipe in 20 minutes"
