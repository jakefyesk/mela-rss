# mela-rss

Auto-curated recipe RSS feeds that import cleanly into the [Mela](https://mela.recipes) recipe app.

Mela subscribes to an RSS feed and, for each item, follows `<link>`, fetches that
page, and extracts a recipe from its schema.org/Recipe **JSON-LD** (with an ML
fallback). `mela-rss` produces feeds + pages that satisfy that contract:

- **link-through** sources (clean JSON-LD already) → the feed item points at the
  original page; Mela extracts natively.
- **rehost** sources (no usable JSON-LD, or no web page at all like Instagram) →
  we scrape/parse the recipe, render our own page carrying clean JSON-LD, and
  point the feed item at it.

The site is static (GitHub Pages), rebuilt every 6h by a GitHub Action.

## Subscribe in Mela

Add the unified feed in **Mela → Feeds → +**:

```
https://<owner>.github.io/mela-rss/feed.xml
```

Per-source feeds live at `…/feeds/<source>.xml`. Retroactive back-catalogs are
also published as one-tap `…/bundles/<source>.melarecipes` bundles.

## Sources

Configured in [`sources.yaml`](sources.yaml). Current roster:

| Source | Mode | Notes |
| --- | --- | --- |
| **finntonry** | rehost | Instagram captions → recipe (free; see below) |
| **Joshua Weissman** | rehost | site has no JSON-LD; we re-emit clean JSON-LD |
| Ethan Chlebowski | link-through | high-protein, technique-driven |
| Andy Cooks | link-through | |
| Justine Snacks | link-through | |
| Jamie Oliver | link-through | |
| finntonry (Mob) | link-through | optional reliable backbone, disabled by default |

Add a source by editing `sources.yaml` — usually no code needed.

### finntonry / Instagram (free, no paid API)

Instagram has no recipe page for Mela to read, so it's a **rehost** source and
the most fragile part. The pipeline is deliberately free:

1. You curate a seed list of post URLs in [`sources/finntonry_posts.txt`](sources/finntonry_posts.txt).
   The most reliable option is to **paste the caption inline** (zero scraping).
2. `caption.py` splits the caption into sections with free regex heuristics.
3. We render a recipe page (image + content). When the heuristics are confident,
   we emit Recipe JSON-LD; otherwise we let **Mela's own ML importer** read the
   visible caption. Either way an image is guaranteed (self-hosted + `og:image`).

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
pytest                                   # 42 tests, all offline
# build locally (writes docs/ + data/catalog.json)
PYTHONPATH=src python -m melarss.build --base-url http://localhost:8000
python -m http.server 8000 --directory docs   # then point Mela at http://<lan-ip>:8000/feed.xml
```

Full backfill + bundles: `PYTHONPATH=src python -m melarss.build --backfill`.

## Layout

```
src/melarss/      pipeline (models, extract, caption, discovery/, rehost, feeds, catalog, build)
templates/        recipe.html.j2 (JSON-LD page)
sources.yaml      source roster
sources/          finntonry seed list
data/catalog.json durable state (dedup + backfill + future personalization)
docs/             GitHub Pages output (feeds, pages, bundles)
tests/            offline test suite + fixtures
```

Design rationale and future plans (conversational personalized feed, cocktails)
are in the approved plan.
