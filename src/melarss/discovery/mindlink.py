"""MindLink: forward recipes saved in MindLink into the Mela feeds.

MindLink (https://github.com/jakefyesk/MindLink) is the user's private "save
anything" hub. It classifies each save with Claude and exposes a read-only REST
API. This adapter *pulls* everything MindLink classified as a ``recipe`` and
turns it into a ``rehost`` recipe, tagged so the user can tell — in the feed and
inside Mela — that it's one they saved via MindLink.

Why pull (not a webhook): mela-rss is a static site rebuilt by a GitHub Action;
it has no server to receive a push. Each build asks MindLink for its recipes,
exactly the flow MindLink's own docs describe ("check my MindLink for recipes
… and ingest them"). Only *new* items (not already in the catalog) get the
per-item detail fetch, so steady-state cost is one cheap list call.

Config (no secrets in the repo):
  * ``MINDLINK_API_URL``  – base URL of the MindLink app, e.g.
    ``https://mind-link.vercel.app`` (may also be set as the source's ``url``).
  * ``MINDLINK_TOKEN``    – a MindLink API token with the ``read`` scope.
When either is missing the adapter is a graceful no-op (empty discovery), so an
unconfigured fork still builds all its other sources.

The recipe body is parsed from MindLink's OCR/extracted text with the same free
``caption`` heuristics used for Instagram: when they find both ingredients and
steps we emit Recipe JSON-LD; otherwise the rehosted page carries the visible
text and Mela's own importer reads it.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from urllib.parse import quote

from .. import caption as caption_mod
from .. import normalize
from ..config import SourceConfig
from ..models import Mode, Recipe

log = logging.getLogger("melarss")

PROVENANCE = "MindLink"  # marker shown in the feed / Mela / page
_PAGE_SIZE = 50


def _ref_for(item_id: str) -> str:
    """Stable dedup basis for a MindLink item. Keyed on MindLink's immutable item
    id (not the saved URL, which may be absent for notes/images), so re-enriching
    an item never changes its <guid>."""
    return f"mindlink://item/{item_id}"


def _safe_link(url: str) -> str:
    """Only keep http(s) URLs for the rendered "source" link — MindLink stores
    whatever the user saved, so refuse javascript:/data: and other schemes."""
    u = (url or "").strip()
    return u if u[:7].lower() == "http://" or u[:8].lower() == "https://" else ""


def _pick_image(media: list[dict]) -> str:
    """Prefer MindLink's generated thumbnail, else any image original. These are
    short-lived signed Storage URLs; the build self-hosts them so they persist."""
    for m in media or []:
        if m.get("kind") == "thumbnail" and m.get("url"):
            return str(m["url"])
    for m in media or []:
        if str(m.get("mime_type", "")).startswith("image/") and m.get("url"):
            return str(m["url"])
    return ""


class MindLinkAdapter:
    mode = Mode.REHOST

    def __init__(self, cfg: SourceConfig, http) -> None:
        self.cfg = cfg
        self.http = http
        self.name = cfg.name
        self.category = cfg.category
        self.base = (cfg.url or os.environ.get("MINDLINK_API_URL") or "").rstrip("/")
        self.token = os.environ.get("MINDLINK_TOKEN") or ""
        # ref -> list-row (cheap projection), reused so fetch_and_parse only pays
        # the detail call for genuinely new items.
        self._rows: dict[str, dict] = {}
        # ref -> save/enrich date, used as the publish date (feeds order by it).
        self.date_hints: dict[str, datetime] = {}
        # dedup_key -> did caption heuristics find both ingredients and steps?
        self.confident: dict[str, bool] = {}

    # -- helpers -----------------------------------------------------------
    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def _get_json(self, url: str) -> dict:
        return json.loads(self.http.get(url, headers=self._auth()))

    def _limit(self) -> int:
        # Steady-state window of newest recipes to consider each run; the catalog
        # dedups, so this only bounds how far back a single build looks.
        return max(self.cfg.max_new_per_run * 3, 30)

    # -- adapter protocol --------------------------------------------------
    def discover(self) -> list[str]:
        self._rows = {}
        self.date_hints = {}
        if not (self.base and self.token):
            log.info(
                "[%s] MindLink not configured (set MINDLINK_API_URL + MINDLINK_TOKEN); skipping",
                self.name,
            )
            return []

        limit = self._limit()
        refs: list[str] = []
        cursor: str | None = None
        try:
            while len(refs) < limit:
                url = f"{self.base}/api/v1/items?type=recipe&limit={_PAGE_SIZE}"
                if cursor:
                    url += f"&cursor={quote(cursor)}"
                data = self._get_json(url)
                items = data.get("items") or []
                for row in items:
                    rid = row.get("id")
                    if not rid:
                        continue
                    ref = _ref_for(str(rid))
                    if ref in self._rows:
                        continue
                    self._rows[ref] = row
                    hint = normalize.parse_date(row.get("enriched_at") or row.get("created_at"))
                    if hint is not None:
                        self.date_hints[ref] = hint
                    refs.append(ref)
                cursor = data.get("next_cursor")
                if not cursor or not items:
                    break
        except Exception as exc:  # noqa: BLE001 — degrade to whatever we collected
            log.warning("[%s] MindLink discovery failed: %s", self.name, exc)

        return refs[:limit]

    def fetch_and_parse(self, ref: str) -> Recipe | None:
        row = self._rows.get(ref)
        if row is None:
            return None
        rid = str(row.get("id") or "")
        if not rid:
            return None

        # Full detail carries the recipe body (OCR/extracted text) and signed
        # media URLs the list projection omits. Degrade to the list row on error.
        try:
            detail = self._get_json(f"{self.base}/api/v1/items/{quote(rid, safe='')}")
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] MindLink detail fetch failed for %s: %s", self.name, rid, exc)
            detail = row

        title = (detail.get("title") or row.get("title") or "").strip()
        summary = (detail.get("summary") or "").strip()
        tags = detail.get("tags") or row.get("tags") or []
        metadata = detail.get("metadata") or {}
        media = detail.get("media") or []

        body = (
            (detail.get("ocr_text") or "").strip()
            or (detail.get("content_md") or "").strip()
            or summary
        )
        parsed = caption_mod.parse_caption(body) if body else caption_mod.ParsedCaption()

        image_url = _pick_image(media)
        # Prefer MindLink's clean enriched title; fall back to the parsed one.
        final_title = title or parsed.title or "Untitled recipe"
        if not (final_title.strip() or image_url or body):
            return None

        author = ""
        if isinstance(metadata, dict):
            author = str(metadata.get("author") or "").strip()

        recipe = Recipe(
            dedup_key=normalize.make_dedup_key(self.cfg.name, ref),
            source=self.cfg.name,
            source_url=_safe_link(detail.get("url") or row.get("url") or ""),
            mode=Mode.REHOST,
            category=self.cfg.category,
            saved_via=PROVENANCE,
            title=final_title,
            text=summary or parsed.description,
            ingredients="\n".join(parsed.ingredients),
            instructions="\n".join(parsed.instructions),
            nutrition=parsed.nutrition,
            yield_=parsed.yield_,
            categories=normalize.split_categories(*tags),
            image_url=image_url,
            author=author or self.cfg.rehost_author or "",
            published_at=None,  # build.py fills from date_hints (save/enrich date)
        )
        self.confident[recipe.dedup_key] = parsed.confident
        return recipe
