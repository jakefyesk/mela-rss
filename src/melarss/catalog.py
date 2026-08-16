"""Durable state store (data/catalog.json).

Keyed by dedup_key. Preserves first-seen data across runs so guids stay stable
and backfilled history is retained; this file is also the substrate the future
personalization feed will read.

It is also where duplicate recipes are stopped. Two guards, because they catch
different shapes of the same problem:

  * the dedup_key itself folds locale variants of a URL together (normalize),
    which handles sitemaps that list /x, /de/x and /es/x for one recipe;
  * a `content_fingerprint` index catches the rest — the same recipe published
    at genuinely different URLs (moved slug, AMP copy, www vs apex). The loser
    is remembered in `aliases` so its URL is never re-fetched, and never turns
    into a second recipe.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Category, Mode, Recipe
from .normalize import canonicalize_url, dedup_url, make_dedup_key

SCHEMA_VERSION = 2

# Fields persisted for each recipe (order = readable diffs in git).
_PERSISTED = [
    "dedup_key", "source", "source_url", "page_url", "mode", "category",
    "saved_via",
    "title", "text", "author", "cuisine", "categories",
    "ingredients", "instructions", "notes", "nutrition", "yield_",
    "prep_time", "cook_time", "total_time",
    "prep_minutes", "cook_minutes", "total_minutes",
    "image_url", "local_image",
]

_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


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


def _norm_block(text: str) -> str:
    """Whitespace/case-insensitive form of a multi-line field, for comparison."""
    lines = [" ".join(line.split()).lower() for line in (text or "").split("\n")]
    return "\n".join(line for line in lines if line)


def content_fingerprint(recipe: Recipe) -> str:
    """Identity of a recipe's *content*, independent of where it came from.

    Excludes every URL — including the image, which rehost rewrites to our own
    base URL — so one recipe published at two addresses fingerprints identically.

    Returns "" for anything too thin to identify safely (no title, or neither
    ingredients nor steps); callers must treat "" as "no opinion" rather than
    letting sparse records collide with each other.
    """
    title = " ".join((recipe.title or "").split()).lower()
    ingredients = _norm_block(recipe.ingredients)
    instructions = _norm_block(recipe.instructions)
    if not title or not (ingredients or instructions):
        return ""
    basis = "\u241f".join([title, ingredients, instructions])
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _legacy_dedup_key(source: str, url: str) -> str:
    """The dedup key a URL produced before locale folding existed.

    Used only by the migration, to tell "this record was keyed from its own
    source_url under the old rule" (safe to re-key) apart from "this record is
    keyed on something else entirely" — MindLink keys on an opaque item id, so
    its keys must never be rewritten from source_url.
    """
    return hashlib.sha1(f"{source}|{canonicalize_url(url)}".encode("utf-8")).hexdigest()


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
    rec["content_fingerprint"] = content_fingerprint(recipe)
    return rec


def record_to_recipe(rec: dict) -> Recipe:
    return Recipe(
        dedup_key=rec["dedup_key"],
        source=rec["source"],
        source_url=rec["source_url"],
        mode=Mode(rec["mode"]),
        category=Category(rec.get("category", "recipe")),
        saved_via=rec.get("saved_via", ""),
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


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _survivor_rank(rec: dict) -> tuple:
    """Order duplicates best-first.

    The copy at the un-localized URL wins; then one already published (dropping
    a <guid> Mela has imported is the churn we're trying to avoid); then the one
    we saw first; then the key, so the outcome never depends on dict ordering.
    """
    url = rec.get("source_url") or ""
    localized = 0 if dedup_url(url) == canonicalize_url(url) else 1
    discovered = _parse_iso(rec.get("discovered_at"))
    return (
        localized,
        0 if rec.get("in_feed") else 1,
        _aware(discovered) if discovered else _FAR_FUTURE,
        rec.get("dedup_key") or "",
    )


# Identity of the record we keep — never taken from the copy being dropped, or
# the survivor would start pointing at the duplicate's address.
_MERGE_EXCLUDED = frozenset({"dedup_key", "source", "source_url", "page_url", "mode", "category"})


def _merge_record(survivor: dict, loser: dict) -> None:
    """Fold a duplicate into the record we keep: the earliest first-seen date
    wins, and every field the survivor is missing is taken from the copy, so
    merging can only ever add information."""
    kept = _parse_iso(survivor.get("discovered_at"))
    dropped = _parse_iso(loser.get("discovered_at"))
    if dropped is not None and (kept is None or _aware(dropped) < _aware(kept)):
        survivor["discovered_at"] = loser["discovered_at"]
    for field in (*_PERSISTED, "published_at"):
        if field in _MERGE_EXCLUDED:
            continue
        if not survivor.get(field) and loser.get(field):
            survivor[field] = loser[field]
    # Anything copied in can change the content digests — recompute rather than
    # leave them describing the record as it was before the merge.
    merged = recipe_to_record(record_to_recipe(survivor))
    survivor["content_hash"] = merged["content_hash"]
    survivor["content_fingerprint"] = merged["content_fingerprint"]


class Catalog:
    def __init__(
        self,
        records: dict[str, dict] | None = None,
        failures: dict[str, dict] | None = None,
        aliases: dict[str, dict] | None = None,
    ) -> None:
        self.records: dict[str, dict] = records or {}
        # Negative cache of refs that failed extraction (non-recipe pages etc.),
        # so we don't re-fetch them every run and starve the per-run budget.
        self.failures: dict[str, dict] = failures or {}
        # dedup_key -> the record it duplicates. Written when a ref turns out to
        # be a second copy of a recipe we already have; keeps that ref "known"
        # so discovery stops re-fetching it every run.
        self.aliases: dict[str, dict] = aliases or {}
        self._fingerprints: dict[tuple[str, str], str] | None = None

    @classmethod
    def load(cls, path: str | Path) -> "Catalog":
        p = Path(path)
        if not p.exists():
            return cls({})
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(data.get("recipes", {}), data.get("failures", {}), data.get("aliases", {}))

    def save(self, path: str | Path, now: datetime) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "generated_at": _iso(now),
            "count": len(self.records),
            "recipes": self.records,
            "failures": self.failures,
            "aliases": self.aliases,
        }
        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # -- duplicate guard ---------------------------------------------------
    def _fingerprint_index(self) -> dict[tuple[str, str], str]:
        """(source, fingerprint) -> the dedup_key that owns it. Rebuilt lazily."""
        if self._fingerprints is None:
            index: dict[tuple[str, str], str] = {}
            for key, rec in self.records.items():
                fingerprint = rec.get("content_fingerprint") or content_fingerprint(
                    record_to_recipe(rec)
                )
                if fingerprint:
                    index.setdefault((rec.get("source", ""), fingerprint), key)
            self._fingerprints = index
        return self._fingerprints

    def find_duplicate(self, recipe: Recipe) -> str | None:
        """The key of an existing record with identical content, if any.

        Scoped to the same source: two creators publishing the same dish is a
        real pair of recipes, whereas one source serving one recipe at two URLs
        is the duplication we're guarding against.
        """
        fingerprint = content_fingerprint(recipe)
        if not fingerprint:
            return None
        owner = self._fingerprint_index().get((recipe.source, fingerprint))
        return owner if owner and owner != recipe.dedup_key else None

    def record_alias(
        self, dedup_key: str, source: str, source_url: str, duplicate_of: str, now: datetime
    ) -> None:
        """Remember that `dedup_key` is a second copy of `duplicate_of`, so its
        ref counts as already-known and never costs another fetch."""
        if dedup_key in self.records:
            return
        self.aliases[dedup_key] = {
            "source": source,
            "source_url": source_url,
            "duplicate_of": duplicate_of,
            "recorded_at": _iso(now),
        }

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
        """Known to us — either a stored recipe or a ref we already merged away."""
        return dedup_key in self.records or dedup_key in self.aliases

    def resolve(self, dedup_key: str) -> str | None:
        """The key of the record this ref refers to, following an alias once."""
        if dedup_key in self.records:
            return dedup_key
        alias = self.aliases.get(dedup_key)
        target = alias.get("duplicate_of") if alias else None
        return target if target in self.records else None

    def get_recipe(self, dedup_key: str) -> Recipe | None:
        target = self.resolve(dedup_key)
        rec = self.records.get(target) if target else None
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
        self.aliases.pop(recipe.dedup_key, None)
        self._fingerprints = None  # content may have changed; rebuild on demand
        return changed

    def adopt_source_url(self, dedup_key: str, url: str) -> bool:
        """Repoint a stored recipe at an equivalent URL we now know exists.

        Same recipe, same key, same <guid> — only the address changes. A recipe
        first discovered only at /de/x keeps that URL, because it's the one we
        can prove serves the page; when discovery later offers /x for the same
        key, that's evidence the canonical URL is real, so adopt it. Refuses any
        URL that doesn't hash to this record's key, so it can never repoint a
        recipe at a different one.
        """
        target = self.resolve(dedup_key)
        rec = self.records.get(target) if target else None
        if rec is None or not url or rec.get("source_url") == url:
            return False
        if self.resolve(make_dedup_key(rec.get("source", ""), url)) != target:
            return False
        if rec.get("page_url") == rec.get("source_url"):
            rec["page_url"] = url  # link_through points at the source page
        rec["source_url"] = url
        return True

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

    # -- self-healing repair -----------------------------------------------
    @staticmethod
    def _rekeyed(key: str, rec: dict) -> str | None:
        """The key this record *should* have today, or None if its key isn't
        derived from its own URL.

        A record counts as URL-derived when its stored key is reproducible from
        its stored source_url under either the current or the previous rule.
        MindLink keys on an opaque item id rather than the saved URL, so it
        matches neither and is correctly left alone.
        """
        source, url = rec.get("source", ""), rec.get("source_url", "")
        if not url:
            return None
        current = make_dedup_key(source, url)
        if key == current or key == _legacy_dedup_key(source, url):
            return current
        return None

    def deduplicate(self, now: datetime) -> dict[str, int]:
        """Merge records that are copies of one another. Idempotent.

        Run at the start of every build, so the state file self-heals instead of
        carrying a duplicate into the feed forever. Two passes, mirroring the two
        guards: re-key records whose URL now folds onto a different key (locale
        variants), then merge same-source records with identical content. The
        negative cache is re-keyed alongside so its backoff isn't orphaned.
        """
        stats = {"aliased": 0, "merged": 0, "failures_rekeyed": 0, "failures_dropped": 0}

        # 1. Fold records whose URL now hashes to a different key than the one
        #    they are stored under (the locale variants). A record is never
        #    *renamed*: its key is its published <guid>, and a recipe Mela has
        #    already imported must not come back wearing a new one. So either it
        #    merges into the record already holding the folded key, or — when
        #    nothing is there — the folded key is registered as an alias of it,
        #    which is equally enough to stop a second copy being created.
        for key in sorted(self.records, key=lambda k: _survivor_rank(self.records[k])):
            rec = self.records.get(key)
            if rec is None:  # merged away earlier in this pass
                continue
            target = self._rekeyed(key, rec)
            if target is None or target == key:
                continue
            survivor = self.records.get(target)
            if survivor is None:
                if target not in self.aliases:
                    self.record_alias(target, rec.get("source", ""), rec.get("source_url", ""), key, now)
                    stats["aliased"] += 1
            else:
                _merge_record(survivor, self.records.pop(key))
                self.record_alias(key, rec.get("source", ""), rec.get("source_url", ""), target, now)
                stats["merged"] += 1

        # 2. Merge same-source records whose content is identical.
        groups: dict[tuple[str, str], list[str]] = {}
        for key, rec in self.records.items():
            fingerprint = rec.get("content_fingerprint")
            if fingerprint is None:  # catalog written before fingerprints existed
                fingerprint = content_fingerprint(record_to_recipe(rec))
                rec["content_fingerprint"] = fingerprint
            if fingerprint:
                groups.setdefault((rec.get("source", ""), fingerprint), []).append(key)
        for (source, _fingerprint), keys in groups.items():
            if len(keys) < 2:
                continue
            keys.sort(key=lambda k: _survivor_rank(self.records[k]))
            survivor = self.records[keys[0]]
            for dup in keys[1:]:
                rec = self.records.pop(dup)
                _merge_record(survivor, rec)
                self.record_alias(dup, source, rec.get("source_url", ""), keys[0], now)
                stats["merged"] += 1

        # 3. Keep the negative cache pointing at the same keys the crawler now
        #    computes, and drop entries for refs that resolved after all.
        for key in list(self.failures):
            target = self._rekeyed(key, self.failures[key])
            if target is None or target == key:
                continue
            rec = self.failures.pop(key)
            if target in self.failures:
                stats["failures_dropped"] += 1
            else:
                self.failures[target] = rec
                stats["failures_rekeyed"] += 1
        for key in list(self.failures):
            if key in self.records or key in self.aliases:
                del self.failures[key]
                stats["failures_dropped"] += 1

        # 4. An alias pointing at a record that no longer exists would keep a
        #    real recipe permanently "known" and therefore never re-discovered.
        for key, alias in list(self.aliases.items()):
            if key in self.records or alias.get("duplicate_of") not in self.records:
                del self.aliases[key]

        self._fingerprints = None
        return stats
