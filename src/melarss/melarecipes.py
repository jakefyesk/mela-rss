"""Export recipes as Mela's own files.

`.melarecipe`  = a single JSON file (fields per Mela's file-format docs).
`.melarecipes` = a ZIP of many `.melarecipe` files (bulk one-tap import).

Useful for the Joshua Weissman retroactive backfill: instead of the RSS
drip-feeding hundreds of items, publish one bundle the user imports once.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from .images import image_to_base64
from .models import Recipe
from .normalize import slugify


def recipe_to_melarecipe(recipe: Recipe, http=None, *, include_image: bool = True) -> dict:
    images: list[str] = []
    if include_image and http is not None and recipe.image_url:
        b64 = image_to_base64(recipe.image_url, http)
        if b64:
            images.append(b64)
    return {
        "id": recipe.mela_id(),  # required, non-empty
        "title": recipe.title,
        "text": recipe.text,
        "images": images,
        "categories": [c for c in recipe.categories if "," not in c],
        "yield": recipe.yield_,
        "prepTime": recipe.prep_time,
        "cookTime": recipe.cook_time,
        "totalTime": recipe.total_time,
        "ingredients": recipe.ingredients,
        "instructions": recipe.instructions,
        "notes": recipe.notes,
        "nutrition": recipe.nutrition,
        "link": recipe.source_url,
    }


def export_bundle(recipes: list[Recipe], out_path: str | Path, http=None) -> int:
    """Write a .melarecipes ZIP. Returns the number of recipes written."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    seen: set[str] = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for recipe in recipes:
            data = recipe_to_melarecipe(recipe, http=http)
            base = slugify(recipe.title) or recipe.dedup_key[:12]
            name = f"{base}.melarecipe"
            n = 1
            while name in seen:
                n += 1
                name = f"{base}-{n}.melarecipe"
            seen.add(name)
            zf.writestr(name, json.dumps(data, ensure_ascii=False, indent=2))
            written += 1
    return written
