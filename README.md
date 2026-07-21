# mela-rss

Auto-curated RSS feeds — **food recipes** and **cocktails**, kept separate — that
import cleanly into the [Mela](https://mela.recipes) recipe app.

Mela subscribes to an RSS feed and, for each item, follows `<link>`, fetches that
page, and extracts a recipe from its schema.org/Recipe **JSON-LD** (with an ML
fallback). `mela-rss` produces feeds + pages that satisfy that contract:

- **link-through** sources (clean JSON-LD already) → the feed item points at the
  original page; Mela extracts natively.
- **rehost** sources (no usable JSON-LD, or no web page at all like Instagram) →
  we scrape/parse the recipe, render our own page carrying clean JSON-LD, and
  point the feed item at it.

Feeds are ordered newest-first by each recipe's **publish date**, not by import
time or source. That date comes from the page's schema.org `datePublished`; when
a page omits it, we fall back to the date discovery already sees — the sitemap
`<lastmod>` or the source feed's `<pubDate>` — so a whole source doesn't collapse
onto one bulk-import timestamp. Existing undated recipes are backfilled from that
same discovery data on the next run (no re-fetch).

The site is static (GitHub Pages), rebuilt every 6h by a GitHub Action.

> **One-time setup — enable GitHub Pages.** In **Settings → Pages → Build and
> deployment**, set **Source** to **GitHub Actions**. Until this is done the
> `deploy` job fails with `Failed to create deployment (status: 404) … Ensure
> GitHub Pages has been enabled`: the workflow's build/upload succeeds, but the
> Pages site it deploys to doesn't exist yet. The built-in `GITHUB_TOKEN` cannot
> turn Pages on, so this can't be automated from the workflow with the default
> token — it's a single manual toggle (or a PAT with admin/pages-write wired
> into `actions/configure-pages` if you want it fully hands-off).

## Subscribe in Mela

There are two clean, single-topic feeds — add either (or both) in
**Mela → Feeds → +**. They stay separate, so cocktails never mix into your
cooking feed:

```
https://<owner>.github.io/mela-rss/recipes.xml     # food recipes
https://<owner>.github.io/mela-rss/cocktails.xml   # cocktails
```

Prefer everything in one place? The combined firehose still lives at
`…/feed.xml`. Per-source feeds live at `…/feeds/<source>.xml`. Retroactive
back-catalogs are also published as one-tap `…/bundles/<source>.melarecipes`
bundles.

Which feed a source lands in is set by its `category` in
[`sources.yaml`](sources.yaml) (`recipe` — the default — or `cocktail`).

## Sources

Configured in [`sources.yaml`](sources.yaml). Each source has a `category`
(`recipe` — the default — or `cocktail`) that routes it to `recipes.xml` or
`cocktails.xml`. Current roster:

### Recipes (`recipes.xml`)

| Source | Mode | Notes |
| --- | --- | --- |
| **finntonry** | rehost | Instagram captions → recipe (free; see below) |
| **Joshua Weissman** | rehost | site has no JSON-LD; we re-emit clean JSON-LD |
| Ethan Chlebowski | link-through | high-protein, technique-driven |
| Andy Cooks | link-through | |
| Justine Snacks | link-through | |
| Jamie Oliver | link-through | |
| finntonry (Mob) | link-through | optional reliable backbone, disabled by default |

### Cocktails (`cocktails.xml`)

All Instagram-first, so all **rehost** via curated caption seed lists (same free
pipeline as finntonry — see below). Each has a seed file under `sources/`.

| Source | Seed list | Notes |
| --- | --- | --- |
| More Savory Goods | `sources/moresavorygoods_posts.txt` | creator |
| Kevin Kos | `sources/kevinkos_posts.txt` | creator (site `kevinkos.com` — feed candidate if egress allows) |
| Jean-Félix Desfossés | `sources/jfdesfosses_posts.txt` | creator |
| Jordan Hughes (Cocktail Camera) | `sources/cocktailcamera_posts.txt` | creator |
| Very Good Drinks | `sources/verygooddrinks_posts.txt` | creator |
| Drinks by Evie | `sources/drinksbyevie_posts.txt` | creator |
| Mother Cocktail Bar | `sources/mother_cocktail_bar_posts.txt` | bar (Toronto) |
| PCH — Pacific Cocktail Haven | `sources/pch_sf_posts.txt` | bar (SF) |
| Amour Drink | `sources/amourdrink_posts.txt` | creator (`amourdrink.tv`) |

Add a source by editing `sources.yaml` — usually no code needed. Add a cocktail
by pasting an Instagram caption into that source's seed file (see below).

### MindLink (recipes you saved yourself)

[MindLink](https://github.com/jakefyesk/MindLink) is a private "save anything"
hub: you share a recipe from your phone, it's auto-classified as a `recipe`, and
kept in your library. The **`mindlink`** source pulls those recipes into the
feeds so anything you save shows up in Mela — tagged so you can tell it apart
from the crawled sources.

Every MindLink recipe is marked **Saved via MindLink** three ways:

- a **`MindLink` category** in the recipe's JSON-LD → shows as a filterable
  category chip *inside the Mela app*;
- a `<category>MindLink</category>` on the RSS `<item>` (visible in any feed
  reader); and
- a **🔖 Saved via MindLink** badge on the rehosted recipe page.

It's a **pull**: each 6-hourly build asks MindLink's REST API for its recipes
(only genuinely new ones cost a detail fetch), parses the recipe from the saved
caption/OCR text with the same free heuristics as the Instagram sources, and
rehosts it. Configure it with two repo settings (Settings → Secrets and
variables → Actions) — leave them unset and the source is simply a no-op:

| Kind     | Name               | Value                                                   |
| -------- | ------------------ | ------------------------------------------------------- |
| Variable | `MINDLINK_API_URL` | your MindLink base URL, e.g. `https://mind-link.vercel.app` |
| Secret   | `MINDLINK_TOKEN`   | a MindLink API token with the `read` scope              |

Mint the token in MindLink under **Settings → API tokens**. See
[MindLink → docs/integrations.md](https://github.com/jakefyesk/MindLink/blob/main/docs/integrations.md)
for the other direction (its push webhooks, REST API, and MCP server).

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
pytest                                   # all offline
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

Design rationale and future plans (conversational personalized feed) are in the
approved plan.
