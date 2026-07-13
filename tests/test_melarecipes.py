import json
import zipfile

from melarss import melarecipes
from melarss.models import Mode, Recipe


def make(key="k1", title="Dish", cats=None):
    return Recipe(
        dedup_key=key,
        source="s",
        source_url="https://src/x",
        mode=Mode.REHOST,
        title=title,
        ingredients="1 cup flour\n2 eggs",
        instructions="Mix.\nBake.",
        categories=cats if cats is not None else ["Main"],
        prep_time="PT10M",
    )


def test_recipe_to_melarecipe_fields():
    m = melarecipes.recipe_to_melarecipe(make(), http=None)
    assert m["id"] == "k1"  # required, non-empty
    assert m["ingredients"] == "1 cup flour\n2 eggs"
    assert m["prepTime"] == "PT10M"
    assert m["link"] == "https://src/x"
    assert m["images"] == []  # no http -> no image embedded


def test_melarecipe_strips_comma_categories():
    m = melarecipes.recipe_to_melarecipe(make(cats=["Main, Dinner", "Quick"]), http=None)
    assert all("," not in c for c in m["categories"])


def test_export_bundle_zip(tmp_path):
    out = tmp_path / "s.melarecipes"
    n = melarecipes.export_bundle([make("k1", "Alpha"), make("k2", "Beta")], out, http=None)
    assert n == 2
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "alpha.melarecipe" in names
        assert "beta.melarecipe" in names
        data = json.loads(zf.read("alpha.melarecipe"))
        assert data["title"] == "Alpha"


def test_export_bundle_dedupes_filenames(tmp_path):
    out = tmp_path / "s.melarecipes"
    melarecipes.export_bundle([make("k1", "Same"), make("k2", "Same")], out, http=None)
    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == ["same-2.melarecipe", "same.melarecipe"]
