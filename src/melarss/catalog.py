"""Durable state store (data/catalog.json).

Keyed by dedup_key. Preserves first-seen data across runs so guids stay stable
and backfilled history is retained; this file is also the substrate the future
personalization feed will read.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Category, Mode, Recipe

SCHEMA_VERSION = 1

# Fields persisted for each recipe (order = readable diffs in git).
_PERSISTED = [
    "dedup_key", "source", "source_url", "page_url", "mode", "category",
    "title", "text", "author", "cuisine", "categories",
    "ingredients", "instructions", "notes", "nutrition", "yield_",
    "prep_time", "cook_time", "total_time",
    "prep_minutes", "cook_minutes", "total_minutes",
    "image_url", "local_image",
]


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def content_hash(recipe: Recipe) -> str:
    basis = "␟".join(
        [recipe.title, recipe.ingredients, recipe.instructions, recipe.image_url]
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def recipe_to_record(recipe: Recipe) -> dict:
    rec = {}
    for field_name in _PERSISTED:
        value = getattr(recipe, field_name)
        if isinstance(value, (Mode, Category)):
            value = value.value
        rec[field_name] = value
    rec["published_at"] = _iso(recipe.published_at)
    rec["discovered_at"] = _iso(recipe.discovered_at)
    rec["content_hash"] = content_hash(recipe)
    return rec


def record_to_recipe(rec: dict) -> Recipe:
    return Recipe(
        dedup_key=rec["dedup_key"],
        source=rec["source"],
        source_url=rec["source_url"],
        mode=Mode(rec["mode"]),
        category=Category(rec.get("category", "recipe")),
        title=rec.get("title", ""),
        text=rec.get("text", ""),
        ingredients=rec.get("ingredients", ""),
        instructions=rec.get("instructions", ""),
        notes=rec.get("notes", ""),
        nutrition=rec.get("nutrition", ""),
        yield_=rec.get("yield_", ""),
        prep_time=rec.get("prep_time", ""),
        cook_time=rec.get("cook_time", ""),
        total_time=rec.get("total_time", ""),
        categories=list(rec.get("categories", []) or []),
        cuisine=rec.get("cuisine", ""),
        image_url=rec.get("image_url", ""),
        local_image=rec.get("local_image", ""),
        author=rec.get("author", ""),
        page_url=rec.get("page_url", ""),
        published_at=_parse_iso(rec.get("published_at")),
        discovered_at=_parse_iso(rec.get("discovered_at")),
        prep_minutes=rec.get("prep_minutes"),
        cook_minutes=rec.get("cook_minutes"),
        total_minutes=rec.get("total_minutes"),
    )


class Catalog:
    def __init__(
        self,
        records: dict[str, dict] | None = None,
        failures: dict[str, dict] | None = None,
    ) -> None:
        self.records: dict[str, dict] = records or {}
        # Negative cache of refs that failed extraction (non-recipe pages etc.),
        # so we don't re-fetch them every run and starve the per-run budget.
        self.failures: dict[str, dict] = failures or {}

    @classmethod
    def load(cls, path: str | Path) -> "Catalog":
        p = Path(path)
        if not p.exists():
            return cls({})
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(data.get("recipes", {}), data.get("failures", {}))

    def save(self, path: str | Path, now: datetime) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "generated_at": _iso(now),
            "count": len(self.records),
            "recipes": self.records,
            "failures": self.failures,
        }
        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # -- negative cache ----------------------------------------------------
    def is_suppressed(self, dedup_key: str, now: datetime) -> bool:
        """True if this ref failed recently and isn't due for a retry yet."""
        rec = self.failures.get(dedup_key)
        if not rec:
            return False
        nxt = _parse_iso(rec.get("next_retry"))
        return nxt is not None and now < nxt

    def record_failure(self, source: str, dedup_key: str, source_url: str, now: datetime) -> None:
        rec = self.failures.get(dedup_key, {"attempts": 0})
        attempts = int(rec.get("attempts", 0)) + 1
        # Exponential backoff, capped at 30 days.
        backoff_days = min(2 ** min(attempts, 5), 30)
        self.failures[dedup_key] = {
            "source": source,
            "source_url": source_url,
            "attempts": attempts,
            "last_attempt": _iso(now),
            "next_retry": _iso(now + timedelta(days=backoff_days)),
        }

    def clear_failure(self, dedup_key: str) -> None:
        self.failures.pop(dedup_key, None)

    def has(self, dedup_key: str) -> bool:
        return dedup_key in self.records

    def get_recipe(self, dedup_key: str) -> Recipe | None:
        rec = self.records.get(dedup_key)
        return record_to_recipe(rec) if rec else None

    def upsert(self, recipe: Recipe, now: datetime, *, backfill: bool = False) -> bool:
        """Insert or update. Preserves discovered_at and (crucially) the original
        page_url/slug. Returns True if the content changed (or is new)."""
        existing = self.records.get(recipe.dedup_key)
        if existing:
            # Preserve immutable first-seen fields.
            recipe.discovered_at = _parse_iso(existing.get("discovered_at")) or now
            if existing.get("page_url") and not recipe.page_url:
                recipe.page_url = existing["page_url"]
            if existing.get("local_image") and not recipe.local_image:
                recipe.local_image = existing["local_image"]
            changed = content_hash(recipe) != existing.get("content_hash")
        else:
            recipe.discovered_at = recipe.discovered_at or now
            changed = True

        rec = recipe_to_record(recipe)
        rec["last_seen_at"] = _iso(now)
        rec["in_feed"] = existing.get("in_feed", False) if existing else False
        rec["backfill"] = backfill if not existing else existing.get("backfill", backfill)
        self.records[recipe.dedup_key] = rec
        return changed

    def recipes_for_source(self, source: str) -> list[Recipe]:
        return [
            record_to_recipe(r) for r in self.records.values() if r.get("source") == source
        ]

    def all_recipes(self, category: str | None = None) -> list[Recipe]:
        out = []
        for r in self.records.values():
            if category and r.get("category") != category:
                continue
            out.append(record_to_recipe(r))
        return out

    def mark_in_feed(self, dedup_keys: set[str]) -> None:
        for key, rec in self.records.items():
            rec["in_feed"] = key in dedup_keys
