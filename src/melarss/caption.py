"""Free, pure-Python parsing of Instagram captions into recipe sections.

No API, no cost. Instagram captions from creators like finntonry usually carry
labeled sections ("Ingredients:", "Method:") plus macros. We split them with
regex heuristics. When we *can't* confidently split, ``confident`` is False and
the build leans entirely on Mela's own ML importer (which reads the visible
caption we render on the page).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Section headers we recognise (case-insensitive), mapped to a canonical bucket.
_INGREDIENT_HEADERS = re.compile(
    r"^\s*(ingredients?|you(?:'ll)?\s+need|shopping\s+list|for\s+the\b.*)\s*:?\s*$",
    re.IGNORECASE,
)
_METHOD_HEADERS = re.compile(
    r"^\s*(method|methods|instructions?|directions?|steps?|how\s+to|recipe)\s*:?\s*$",
    re.IGNORECASE,
)
_NUTRITION_HEADERS = re.compile(
    r"^\s*(macros?|nutrition|per\s+serving)\s*:?\s*$", re.IGNORECASE
)
# Inline macro line, e.g. "320cal | 45g protein" or "Calories: 320".
_MACRO_INLINE = re.compile(
    r"(\d+\s*(?:kcal|cal|calories))|(\d+\s*g\s*(?:protein|carbs?|fat|fibre|fiber|sugar))",
    re.IGNORECASE,
)
_YIELD_INLINE = re.compile(
    r"\b(serves?|servings?|makes|yield)\b[:\s]*([0-9]+[^\n]{0,20})", re.IGNORECASE
)
_LEADING_LIST_MARK = re.compile(r"^\s*(?:[-*•·▢▪◦]|\d+[.)]|step\s*\d+[:.)]?)\s*", re.IGNORECASE)
_HASHTAG_LINE = re.compile(r"^\s*(?:#\w+\s*)+$")
_EMOJI_ONLY = re.compile(r"^\s*[^\w\d]+\s*$")


@dataclass
class ParsedCaption:
    title: str = ""
    description: str = ""
    ingredients: list[str] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)
    nutrition: str = ""
    yield_: str = ""
    confident: bool = False  # True when we found both ingredients and steps
    raw: str = ""


def _is_pure_macro(text: str) -> bool:
    """True when a line is essentially just macros (e.g. '400cal | 42g protein'),
    so it can be routed to nutrition from anywhere — but an ingredient that only
    *mentions* protein (e.g. '200g chicken (45g protein)') stays an ingredient."""
    if not _MACRO_INLINE.search(text):
        return False
    residue = re.sub(r"\d+\s*(?:kcal|cal|calories|g|mg)\b", "", text, flags=re.IGNORECASE)
    residue = re.sub(
        r"(?:protein|carbs?|carbohydrates?|fat|fibre|fiber|sugar|calories?|macros?|per\s+serving)",
        "",
        residue,
        flags=re.IGNORECASE,
    )
    residue = re.sub(r"[^\w]", "", residue)
    return len(residue) <= 3


def _strip_emoji(text: str) -> str:
    """Drop emoji/pictographs/variation-selectors, collapse whitespace."""
    kept = [
        ch
        for ch in text
        if unicodedata.category(ch) not in ("So", "Sk", "Cs", "Cf")
        and not ("\U0001F000" <= ch <= "\U0001FAFF")
        and ch != "️"
    ]
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def _clean_line(line: str) -> str:
    return line.replace("⁠", "").rstrip()


def _strip_list_mark(line: str) -> str:
    return _LEADING_LIST_MARK.sub("", line).strip()


def _first_title(lines: list[str]) -> str:
    for line in lines:
        text = line.strip()
        if not text or _HASHTAG_LINE.match(text) or _EMOJI_ONLY.match(text):
            continue
        if _INGREDIENT_HEADERS.match(text) or _METHOD_HEADERS.match(text):
            continue
        # Strip emoji, then drop a trailing "recipe in caption" style CTA.
        text = _strip_emoji(text)
        text = re.sub(
            r"\brecipe\s+(?:in|below|down|👇).*$", "", text, flags=re.IGNORECASE
        )
        text = text.strip(" .!—-·|")
        if text:
            # Title-length guard: captions sometimes open with a full sentence.
            return text[:120].strip()
    return ""


def parse_caption(caption: str) -> ParsedCaption:
    raw = caption or ""
    lines = [_clean_line(l) for l in raw.splitlines()]
    result = ParsedCaption(raw=raw)
    result.title = _first_title(lines)

    section = None  # None | "ingredients" | "instructions" | "nutrition"
    desc_lines: list[str] = []
    macro_bits: list[str] = []

    for line in lines:
        text = line.strip()
        if not text:
            continue
        if _HASHTAG_LINE.match(text):
            continue

        if _INGREDIENT_HEADERS.match(text):
            section = "ingredients"
            # "For the sauce:" style headers double as ingredient group titles.
            m = re.match(r"^\s*for\s+the\b.*$", text, re.IGNORECASE)
            if m:
                result.ingredients.append(f"# {text.rstrip(':').strip()}")
            continue
        if _METHOD_HEADERS.match(text):
            section = "instructions"
            continue
        if _NUTRITION_HEADERS.match(text):
            section = "nutrition"
            continue

        if _is_pure_macro(text) or (section == "nutrition"):
            macro_bits.append(text)
            continue

        ymatch = _YIELD_INLINE.search(text)
        if ymatch and section != "ingredients":
            if not result.yield_:
                result.yield_ = ymatch.group(0).strip()
            continue

        if section == "ingredients":
            result.ingredients.append(_strip_list_mark(text) or text)
        elif section == "instructions":
            result.instructions.append(_strip_list_mark(text) or text)
        elif section == "nutrition":
            macro_bits.append(text)
        else:
            desc_lines.append(text)

    result.description = " ".join(desc_lines).strip()
    result.nutrition = "\n".join(macro_bits).strip()
    result.confident = bool(result.ingredients and result.instructions)
    if not result.title:
        result.title = (result.description[:80] or "Untitled recipe").strip()
    return result
