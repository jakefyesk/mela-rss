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
