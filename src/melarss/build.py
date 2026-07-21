"""Build orchestrator / CLI.

Discovers recipes per source, extracts/normalizes them, self-hosts images and
renders pages for rehost sources, then writes per-source + unified RSS feeds, a
landing page, and (optionally) .melarecipes bundles. Everything is committed
under docs/ + data/catalog.json for GitHub Pages to serve.

Per-source failures are logged and skipped — one dead source never fails the
whole build.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from . import feeds, rehost
from .catalog import Catalog
from .config import SourceConfig, load_config
from .discovery.generic import make_adapter
from .images import self_host_image
from .melarecipes import export_bundle
from .models import Mode, Recipe
from .normalize import make_dedup_key
from .http import DEFAULT_UA, Http

log = logging.getLogger("melarss")

PER_SOURCE_FEED_CAP = 50
UNIFIED_FEED_CAP = 100
CATEGORY_FEED_CAP = 100

# Category -> (published filename, human label). Each is a clean, single-topic
# feed users subscribe to in Mela (recipes.xml vs cocktails.xml); feed.xml stays
# the combined firehose for backward compatibility.
CATEGORY_FEEDS = {
    "recipe": ("recipes.xml", "Recipes"),
    "cocktail": ("cocktails.xml", "Cocktails"),
}


def _process_source(
    cfg: SourceConfig,
    catalog: Catalog,
    http,
    docs_dir: Path,
    base_url: str,
    now: datetime,
    backfill: bool,
) -> int:
    adapter = make_adapter(cfg, http, backfill=backfill)
    try:
        refs = adapter.discover()
    except Exception as exc:  # noqa: BLE001
        log.warning("[%s] discovery failed: %s", cfg.name, exc)
        return 0

    cap = cfg.backfill_limit if (backfill and cfg.backfill_limit) else cfg.max_new_per_run
    new_refs = [
        r
        for r in refs
        if not catalog.has(make_dedup_key(cfg.name, r))
        and not catalog.is_suppressed(make_dedup_key(cfg.name, r), now)
    ]
    to_process = new_refs[:cap]
    log.info(
        "[%s] discovered=%d new=%d processing=%d", cfg.name, len(refs), len(new_refs), len(to_process)
    )

    # Backfill publish dates onto already-known, date-less recipes from the
    # discovery date hints we just fetched (sitemap <lastmod> / feed <pubDate>).
    # This costs no extra network — it reuses the discovery response — and lets
    # the feed order by real publish date instead of a single bulk import time.
    _backfill_published_at(cfg, adapter, catalog, refs, now, backfill)

    added = 0
    for ref in to_process:
        key = make_dedup_key(cfg.name, ref)
        try:
            recipe = adapter.fetch_and_parse(ref)
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] fetch failed for %s: %s", cfg.name, ref, exc)
            catalog.record_failure(cfg.name, key, ref, now)
            continue
        if recipe is None:
            # Non-recipe page (or failed extraction) — suppress so we don't retry
            # it every run and consume the per-run budget.
            catalog.record_failure(cfg.name, key, ref, now)
            continue
        catalog.clear_failure(recipe.dedup_key)

        # No datePublished in the page's JSON-LD? Fall back to the date discovery
        # already gave us (sitemap <lastmod> / feed <pubDate>).
        if recipe.published_at is None:
            recipe.published_at = adapter.date_hints.get(ref)

        if recipe.mode == Mode.REHOST:
            _finalize_rehost(cfg, adapter, recipe, http, docs_dir, base_url)
        else:  # link_through: point at the original page
            recipe.page_url = ref
            if not recipe.image_url:
                log.warning("[%s] no image for %s (Mela may import imageless)", cfg.name, ref)

        catalog.upsert(recipe, now, backfill=backfill)
        added += 1

    return added


def _backfill_published_at(cfg, adapter, catalog: Catalog, refs, now, backfill: bool) -> None:
    """Set published_at on already-known, date-less recipes from discovery date
    hints. Reuses the discovery response (no page re-fetch), so a source that
    was imported before we captured dates gets ordered correctly on the next run.
    """
    hints = getattr(adapter, "date_hints", None)
    if not hints:
        return
    for ref in refs:
        hint = hints.get(ref)
        if hint is None:
            continue
        recipe = catalog.get_recipe(make_dedup_key(cfg.name, ref))
        if recipe is None or recipe.published_at is not None:
            continue
        recipe.published_at = hint
        catalog.upsert(recipe, now, backfill=backfill)


def _finalize_rehost(cfg, adapter, recipe: Recipe, http, docs_dir: Path, base_url: str) -> None:
    relpath = rehost.page_relpath(recipe)
    slug = Path(relpath).stem
    # Self-host the image so Mela always finds it (JSON-LD + og:image + <img>).
    if recipe.image_url:
        img_rel = f"recipes/{recipe.source}/img/{slug}.jpg"
        if self_host_image(recipe.image_url, docs_dir / img_rel, http):
            recipe.local_image = img_rel
            recipe.image_url = f"{base_url}/{img_rel}"
        else:
            # Rehost images are meant to be self-hosted; the source URL may be a
            # short-lived signed/CDN URL (MindLink Storage, IG CDN), so don't bake
            # it into the persisted catalog/feed/JSON-LD where it would rot.
            log.warning("[%s] image download failed for %s", cfg.name, recipe.source_url)
            recipe.image_url = ""
    else:
        log.warning("[%s] no image for %s", cfg.name, recipe.source_url)

    # Caption-parsed sources (Instagram, MindLink) emit Recipe JSON-LD only when
    # the heuristics were confident (found both ingredients and steps); otherwise
    # they let Mela's ML importer read the visible text. Adapters without a
    # `confident` map (crawl sources, which have real JSON-LD) always emit.
    #
    # Exception: a *forwarded* recipe (saved_via) must stay identifiable inside
    # Mela, whose only category surface is the JSON-LD recipeCategory — so always
    # emit at least a marker card carrying its provenance ("MindLink"). Full when
    # confident; otherwise name/image/category only, with no unreliable
    # ingredients/steps (Mela still reads the visible caption on the page).
    confident = getattr(adapter, "confident", None)
    is_confident = True if confident is None else confident.get(recipe.dedup_key, False)
    if recipe.saved_via:
        emit_jsonld, full_jsonld = True, is_confident
    else:
        emit_jsonld, full_jsonld = is_confident, True

    html = rehost.render_recipe_page(recipe, emit_jsonld=emit_jsonld, full_jsonld=full_jsonld)
    page_path = docs_dir / relpath
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(html, encoding="utf-8")
    recipe.page_url = f"{base_url}/{relpath}"


def _write_feeds(configs, catalog: Catalog, docs_dir: Path, base_url: str) -> set[str]:
    feeds_dir = docs_dir / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)
    in_feed: set[str] = set()
    unified: list[Recipe] = []
    by_category: dict[str, list[Recipe]] = {}

    for cfg in configs:
        recipes = catalog.recipes_for_source(cfg.name)
        if not recipes:
            continue
        unified.extend(recipes)
        by_category.setdefault(cfg.category.value, []).extend(recipes)
        xml = feeds.build_feed(
            cfg.name,
            f"{cfg.title()} (mela-rss)",
            f"{base_url}/feeds/{cfg.name}.xml",
            base_url,
            recipes,
            PER_SOURCE_FEED_CAP,
        )
        (feeds_dir / f"{cfg.name}.xml").write_bytes(xml)
        for r in feeds.selected_for_feed(recipes, PER_SOURCE_FEED_CAP):
            in_feed.add(r.dedup_key)

    # Combined firehose (backward-compatible: existing subscribers keep feed.xml).
    xml = feeds.build_feed(
        "all", "All recipes (mela-rss)", f"{base_url}/feed.xml", base_url, unified, UNIFIED_FEED_CAP
    )
    (docs_dir / "feed.xml").write_bytes(xml)
    for r in feeds.selected_for_feed(unified, UNIFIED_FEED_CAP):
        in_feed.add(r.dedup_key)

    # Category feeds — the clean, single-topic feeds to subscribe to (recipes vs
    # cocktails). Written for every category that has an enabled source, even
    # with zero recipes yet, so the subscribe URL is stable and valid from day one.
    for category in sorted({cfg.category.value for cfg in configs}):
        filename, label = CATEGORY_FEEDS.get(category, (f"{category}.xml", category.title()))
        recipes = by_category.get(category, [])
        xml = feeds.build_feed(
            category,
            f"{label} (mela-rss)",
            f"{base_url}/{filename}",
            base_url,
            recipes,
            CATEGORY_FEED_CAP,
        )
        (docs_dir / filename).write_bytes(xml)
        for r in feeds.selected_for_feed(recipes, CATEGORY_FEED_CAP):
            in_feed.add(r.dedup_key)

    catalog.mark_in_feed(in_feed)
    return in_feed


def _write_index(configs, catalog: Catalog, docs_dir: Path, base_url: str, bundles: dict[str, int]) -> None:
    # Group enabled sources by category so the two subscribe feeds (recipes vs
    # cocktails) each list their own roster underneath.
    cats: dict[str, list] = {}
    for cfg in configs:
        count = len(catalog.recipes_for_source(cfg.name))
        cats.setdefault(cfg.category.value, []).append((cfg, count))

    sections = []
    for category in sorted(cats):
        filename, label = CATEGORY_FEEDS.get(category, (f"{category}.xml", category.title()))
        total = sum(c for _, c in cats[category])
        rows = []
        for cfg, count in cats[category]:
            if not count:
                # No per-source feed file exists yet (only written once a source
                # has content), so don't link to a URL that would 404.
                rows.append(
                    f'<li><strong>{cfg.title()}</strong> — awaiting first import</li>'
                )
                continue
            bundle = ""
            if cfg.name in bundles:
                bundle = f' · <a href="{base_url}/bundles/{cfg.name}.melarecipes">bundle ({bundles[cfg.name]})</a>'
            rows.append(
                f'<li><strong>{cfg.title()}</strong> — '
                f'<a href="{base_url}/feeds/{cfg.name}.xml">feed</a> · {count} items{bundle}</li>'
            )
        sections.append(
            f'<h2>{label}</h2>\n'
            f'<p>Subscribe in Mela: <code>{base_url}/{filename}</code> · {total} items</p>\n'
            f'<ul>\n{chr(10).join(rows)}\n</ul>'
        )

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mela-rss — recipe & cocktail feeds for Mela</title>
<style>body{{font-family:-apple-system,system-ui,sans-serif;max-width:680px;margin:2rem auto;padding:0 1rem;line-height:1.6}}code{{background:#f4f4f4;padding:.1rem .3rem;border-radius:4px}}</style>
</head><body>
<h1>mela-rss</h1>
<p>Auto-curated feeds, ready to import into the <a href="https://mela.recipes">Mela</a> app.
Add either topic feed in Mela → Feeds — they stay separate so cocktails never mix
into your cooking feed:</p>
<p><code>{base_url}/recipes.xml</code><br><code>{base_url}/cocktails.xml</code></p>
<p style="color:#888;font-size:.85rem">Prefer everything in one place? <code>{base_url}/feed.xml</code> is the combined firehose.</p>
{chr(10).join(sections)}
<p style="color:#888;font-size:.85rem">Generated by mela-rss. Items link back to their original sources.</p>
</body></html>
"""
    (docs_dir / "index.html").write_text(html, encoding="utf-8")


def _write_bundles(configs, catalog: Catalog, docs_dir: Path, http) -> dict[str, int]:
    bundles_dir = docs_dir / "bundles"
    counts: dict[str, int] = {}
    for cfg in configs:
        recipes = catalog.recipes_for_source(cfg.name)
        if not recipes:
            continue
        n = export_bundle(recipes, bundles_dir / f"{cfg.name}.melarecipes", http=http)
        counts[cfg.name] = n
    return counts


def run(
    sources_path: str,
    docs_dir: str,
    catalog_path: str,
    base_url: str,
    *,
    backfill: bool = False,
    make_bundles: bool = False,
    only: list[str] | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    config = load_config(sources_path)
    catalog = Catalog.load(catalog_path)
    docs = Path(docs_dir)
    docs.mkdir(parents=True, exist_ok=True)
    (docs / ".nojekyll").write_text("", encoding="utf-8")

    # --only narrows which sources we PROCESS this run, but feeds/index/bundles
    # are always written from the full enabled set (from the catalog), so a
    # targeted run never clobbers the published unified feed.
    enabled = config.enabled()
    to_process = [c for c in enabled if (not only or c.name in only)]
    added_total = 0
    for cfg in to_process:
        http = Http(
            user_agent=cfg.user_agent or DEFAULT_UA,
            delay_seconds=cfg.request_delay_seconds,
        )
        added_total += _process_source(cfg, catalog, http, docs, base_url, now, backfill)

    _write_feeds(enabled, catalog, docs, base_url)
    bundles: dict[str, int] = {}
    if make_bundles or backfill:
        http = Http(delay_seconds=0.5)
        bundles = _write_bundles(enabled, catalog, docs, http)
    _write_index(enabled, catalog, docs, base_url, bundles)
    catalog.save(catalog_path, now)

    summary = {"added": added_total, "total": len(catalog.records), "bundles": bundles}
    log.info("build complete: %s", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    repo = Path(__file__).resolve().parent.parent.parent
    parser = argparse.ArgumentParser(description="Build mela-rss feeds")
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://localhost:8000"))
    parser.add_argument("--sources", default=str(repo / "sources.yaml"))
    parser.add_argument("--docs", default=str(repo / "docs"))
    parser.add_argument("--catalog", default=str(repo / "data" / "catalog.json"))
    parser.add_argument("--backfill", action="store_true", default=os.environ.get("BACKFILL") == "true")
    parser.add_argument("--bundles", action="store_true")
    parser.add_argument("--only", nargs="*", help="restrict to these source names")
    args = parser.parse_args(argv)

    run(
        args.sources,
        args.docs,
        args.catalog,
        args.base_url.rstrip("/"),
        backfill=args.backfill,
        make_bundles=args.bundles,
        only=args.only,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
