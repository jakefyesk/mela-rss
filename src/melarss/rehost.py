"""Render a rehosted recipe page carrying schema.org/Recipe JSON-LD.

Used only for `rehost` sources (Joshua Weissman, finntonry). The JSON-LD shape
is modelled on thejudge22/mela-to-html: HowToStep instructions with `position`,
a flat `recipeIngredient` array (group `#` headers dropped), ISO-8601 durations.

For finntonry we may deliberately emit the page WITHOUT recipe JSON-LD (when the
caption heuristics weren't confident) so Mela's ML importer parses the visible
caption instead — controlled by the `emit_jsonld` argument.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .models import Recipe
from .normalize import slugify

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


@dataclass
class _Line:
    text: str
    is_header: bool


def _env() -> Environment:
    # autoescape=True (not select_autoescape, which keys off extensions and would
    # never match "recipe.html.j2"), so every visible field is HTML-escaped. The
    # JSON-LD block is inserted with |safe after being pre-escaped in render().
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=True,
    )


def ingredient_lines(ingredients: str) -> list[_Line]:
    out = []
    for raw in (ingredients or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            out.append(_Line(text=line.lstrip("#").strip(), is_header=True))
        else:
            out.append(_Line(text=line, is_header=False))
    return out


def ingredient_lines_no_headers(ingredients: str) -> list[str]:
    return [ln.text for ln in ingredient_lines(ingredients) if not ln.is_header]


def instruction_steps(instructions: str) -> list[str]:
    return [s.strip() for s in (instructions or "").split("\n") if s.strip()]


def _human_duration(iso: str) -> str:
    if not iso or not iso.startswith("PT"):
        return iso or ""
    body = iso[2:]
    hours = mins = 0
    num = ""
    for ch in body:
        if ch.isdigit():
            num += ch
        elif ch == "H":
            hours = int(num or 0)
            num = ""
        elif ch == "M":
            mins = int(num or 0)
            num = ""
    parts = []
    if hours:
        parts.append(f"{hours} hr")
    if mins:
        parts.append(f"{mins} min")
    return " ".join(parts)


def build_jsonld(recipe: Recipe) -> dict:
    ld = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe.title,
        "description": recipe.text or None,
        "image": [recipe.image_url] if recipe.image_url else None,
        "author": {"@type": "Person", "name": recipe.author} if recipe.author else None,
        "datePublished": (
            recipe.published_at.date().isoformat()
            if recipe.published_at
            else (recipe.discovered_at.date().isoformat() if recipe.discovered_at else None)
        ),
        "prepTime": recipe.prep_time or None,
        "cookTime": recipe.cook_time or None,
        "totalTime": recipe.total_time or None,
        "recipeYield": recipe.yield_ or None,
        "recipeCategory": recipe.categories or None,
        "recipeCuisine": recipe.cuisine or None,
        "recipeIngredient": ingredient_lines_no_headers(recipe.ingredients) or None,
        "recipeInstructions": [
            {"@type": "HowToStep", "position": i + 1, "text": text}
            for i, text in enumerate(instruction_steps(recipe.instructions))
        ]
        or None,
    }
    return {k: v for k, v in ld.items() if v is not None}


def page_relpath(recipe: Recipe) -> str:
    """Deterministic, collision-free docs-relative path (drives the rehost feed
    <link>). The stable dedup_key suffix prevents two recipes from one source
    with identical title-slugs from overwriting each other's page/image."""
    return f"recipes/{recipe.source}/{slugify(recipe.title)}-{recipe.dedup_key[:8]}.html"


def _escape_for_script(json_str: str) -> str:
    """Neutralise HTML-significant chars so a field containing '</script>' can't
    break out of the <script> block. JSON-LD parsers decode the \\uXXXX escapes
    back, so the data is unchanged."""
    return (
        json_str.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


def render_recipe_page(recipe: Recipe, emit_jsonld: bool = True) -> str:
    jsonld = ""
    if emit_jsonld:
        jsonld = _escape_for_script(
            json.dumps(build_jsonld(recipe), ensure_ascii=False, indent=2)
        )
    # Attach a display-only human duration without mutating the dataclass.
    recipe.total_time_human = _human_duration(recipe.total_time)  # type: ignore[attr-defined]
    template = _env().get_template("recipe.html.j2")
    return template.render(
        recipe=recipe,
        jsonld=jsonld,
        ingredient_lines=ingredient_lines(recipe.ingredients),
        steps=instruction_steps(recipe.instructions),
    )
