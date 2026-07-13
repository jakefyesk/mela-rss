from melarss.config import SourceConfig
from melarss.discovery.instagram import InstagramAdapter, parse_seed_file
from melarss.models import Mode

SEED = """# comment line
https://www.instagram.com/reel/ABC123/
tags: high-protein, chicken

Creamy Peppercorn Chicken
Ingredients:
- 2 chicken breasts
- 100ml cream
Method:
1. Sear the chicken.
2. Add cream and simmer.
400cal | 42g protein

===

https://www.instagram.com/reel/DEF456/

Just a vibe, no recipe here.
"""


def test_parse_seed_file_two_entries_with_directives():
    entries = parse_seed_file(SEED)
    assert len(entries) == 2
    first = entries[0]
    assert first.url == "https://www.instagram.com/reel/ABC123/"
    assert first.tags == ["high-protein", "chicken"]
    assert "Creamy Peppercorn Chicken" in first.caption
    assert "Ingredients:" in first.caption


def test_adapter_discover_and_parse(tmp_path):
    seed = tmp_path / "seed.txt"
    seed.write_text(SEED, encoding="utf-8")
    cfg = SourceConfig(
        name="finntonry",
        mode=Mode.REHOST,
        discovery="instagram",
        seed_file=str(seed),
        rehost_author="Finn Tonry",
    )
    adapter = InstagramAdapter(cfg, http=None)
    refs = adapter.discover()
    assert len(refs) == 2

    recipe = adapter.fetch_and_parse(refs[0])
    assert recipe is not None
    assert recipe.mode == Mode.REHOST
    assert recipe.title == "Creamy Peppercorn Chicken"
    assert "2 chicken breasts" in recipe.ingredients
    assert recipe.author == "Finn Tonry"
    assert "high-protein" in recipe.categories
    # confident because both ingredients and steps were found
    assert adapter.confident[recipe.dedup_key] is True


def test_adapter_unconfident_entry_still_parses(tmp_path):
    seed = tmp_path / "seed.txt"
    seed.write_text(SEED, encoding="utf-8")
    cfg = SourceConfig(
        name="finntonry", mode=Mode.REHOST, discovery="instagram", seed_file=str(seed)
    )
    adapter = InstagramAdapter(cfg, http=None)
    refs = adapter.discover()
    recipe = adapter.fetch_and_parse(refs[1])
    # no ingredients/steps -> not confident (build will lean on Mela's ML)
    assert adapter.confident[recipe.dedup_key] is False
