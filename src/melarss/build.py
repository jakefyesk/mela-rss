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
    new_refs = [r for r in refs if not catalog.has(make_dedup_key(cfg.name, r))]
    to_process = new_refs[:cap]
    log.info(
        "[%s] discovered=%d new=%d processing=%d", cfg.name, len(refs), len(new_refs), len(to_process)
    )

    added = 0
    for ref in to_process:
        try:
            recipe = adapter.fetch_and_parse(ref)
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] fetch failed for %s: %s", cfg.name, ref, exc)
            continue
        if recipe is None:
            continue

        if recipe.mode == Mode.REHOST:
            _finalize_rehost(cfg, adapter, recipe, http, docs_dir, base_url)
        else:  # link_through: point at the original page
            recipe.page_url = ref
            if not recipe.image_url:
                log.warning("[%s] no image for %s (Mela may import imageless)", cfg.name, ref)

        catalog.upsert(recipe, now, backfill=backfill)
        added += 1

    return added


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
            log.warning("[%s] image download failed for %s", cfg.name, recipe.source_url)
    else:
        log.warning("[%s] no image for %s", cfg.name, recipe.source_url)

    # Instagram: emit Recipe JSON-LD only when caption heuristics were confident;
    # otherwise let Mela's ML importer read the visible caption we render.
    emit_jsonld = True
    if cfg.discovery == "instagram":
        emit_jsonld = getattr(adapter, "confident", {}).get(recipe.dedup_key, False)

    html = rehost.render_recipe_page(recipe, emit_jsonld=emit_jsonld)
    page_path = docs_dir / relpath
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text(html, encoding="utf-8")
    recipe.page_url = f"{base_url}/{relpath}"


def _write_feeds(configs, catalog: Catalog, docs_dir: Path, base_url: str) -> set[str]:
    feeds_dir = docs_dir / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)
    in_feed: set[str] = set()
    unified: list[Recipe] = []

    for cfg in configs:
        recipes = catalog.recipes_for_source(cfg.name)
        if not recipes:
            continue
        unified.extend(recipes)
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

    xml = feeds.build_feed(
        "all", "All recipes (mela-rss)", f"{base_url}/feed.xml", base_url, unified, UNIFIED_FEED_CAP
    )
    (docs_dir / "feed.xml").write_bytes(xml)
    for r in feeds.selected_for_feed(unified, UNIFIED_FEED_CAP):
        in_feed.add(r.dedup_key)

    catalog.mark_in_feed(in_feed)
    return in_feed


def _write_index(configs, catalog: Catalog, docs_dir: Path, base_url: str, bundles: dict[str, int]) -> None:
    rows = []
    for cfg in configs:
        count = len(catalog.recipes_for_source(cfg.name))
        if not count:
            continue
        bundle = ""
        if cfg.name in bundles:
            bundle = f' · <a href="{base_url}/bundles/{cfg.name}.melarecipes">bundle ({bundles[cfg.name]})</a>'
        rows.append(
            f'<li><strong>{cfg.title()}</strong> — '
            f'<a href="{base_url}/feeds/{cfg.name}.xml">feed</a> · {count} recipes{bundle}</li>'
        )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mela-rss — recipe feeds for Mela</title>
<style>body{{font-family:-apple-system,system-ui,sans-serif;max-width:680px;margin:2rem auto;padding:0 1rem;line-height:1.6}}code{{background:#f4f4f4;padding:.1rem .3rem;border-radius:4px}}</style>
</head><body>
<h1>mela-rss</h1>
<p>Auto-curated recipe feeds, ready to import into the <a href="https://mela.recipes">Mela</a> app.
Add the unified feed in Mela → Feeds:</p>
<p><code>{base_url}/feed.xml</code></p>
<h2>Per-source feeds</h2>
<ul>
{chr(10).join(rows)}
</ul>
<p style="color:#888;font-size:.85rem">Generated by mela-rss. Recipes link back to their original sources.</p>
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

    configs = [c for c in config.enabled() if (not only or c.name in only)]
    added_total = 0
    for cfg in configs:
        http = Http(
            user_agent=cfg.user_agent or DEFAULT_UA,
            delay_seconds=cfg.request_delay_seconds,
        )
        added_total += _process_source(cfg, catalog, http, docs, base_url, now, backfill)

    _write_feeds(configs, catalog, docs, base_url)
    bundles: dict[str, int] = {}
    if make_bundles or backfill:
        http = Http(delay_seconds=0.5)
        bundles = _write_bundles(configs, catalog, docs, http)
    _write_index(configs, catalog, docs, base_url, bundles)
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
