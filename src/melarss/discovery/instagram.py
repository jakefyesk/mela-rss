"""finntonry / Instagram: free caption -> recipe.

Discovery is a checked-in seed list you curate. Each entry is a post URL and,
optionally, the caption pasted inline (the most reliable + fully free path — no
scraping at all). If a caption isn't pasted, we best-effort fetch it (Instagram
oEmbed when IG_OEMBED_TOKEN is set, else the post page's og:description).

Extraction uses the free `caption` heuristics; the resulting Recipe is `rehost`
(Instagram has no page for Mela to read). When the heuristics aren't confident,
build.py still renders a readable page and lets Mela's own ML importer parse it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from .. import caption as caption_mod
from .. import normalize
from ..config import SourceConfig
from ..models import Mode, Recipe

_SHORTCODE_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)")
_ENTRY_SEP = re.compile(r"^\s*===+\s*$", re.MULTILINE)


@dataclass
class SeedEntry:
    url: str
    caption: str = ""
    image_url: str = ""
    tags: list[str] = field(default_factory=list)


def shortcode(url: str) -> str:
    m = _SHORTCODE_RE.search(url or "")
    return m.group(1) if m else ""


def parse_seed_file(text: str) -> list[SeedEntry]:
    """Parse the seed file.

    Entries are separated by a line of '==='. Within an entry, the first
    non-comment line is the post URL; optional directives `image:` and `tags:`
    may follow; everything after a blank line (or a line 'caption:') is the
    pasted caption. Lines starting with '#' at the top level are comments.
    """
    entries: list[SeedEntry] = []
    for block in _ENTRY_SEP.split(text):
        block = block.strip("\n")
        if not block.strip():
            continue
        lines = block.splitlines()
        url = ""
        image_url = ""
        tags: list[str] = []
        caption_lines: list[str] = []
        in_caption = False
        for line in lines:
            stripped = line.strip()
            if not in_caption:
                if not stripped or stripped.startswith("#"):
                    # blank line after the header switches us into caption mode
                    if url and not stripped:
                        in_caption = True
                    continue
                low = stripped.lower()
                if low.startswith("caption:"):
                    in_caption = True
                    rest = stripped[len("caption:"):].strip()
                    if rest:
                        caption_lines.append(rest)
                    continue
                if low.startswith("image:"):
                    image_url = stripped.split(":", 1)[1].strip()
                    continue
                if low.startswith("tags:"):
                    tags = normalize.split_categories(stripped.split(":", 1)[1])
                    continue
                if "instagram.com/" in low and not url:
                    url = stripped
                    continue
                # any other non-directive line begins the caption
                in_caption = True
                caption_lines.append(line)
            else:
                caption_lines.append(line)
        if url:
            entries.append(
                SeedEntry(
                    url=url,
                    caption="\n".join(caption_lines).strip(),
                    image_url=image_url,
                    tags=tags,
                )
            )
    return entries


def load_seed_entries(path: str | Path) -> list[SeedEntry]:
    p = Path(path)
    if not p.exists():
        return []
    return parse_seed_file(p.read_text(encoding="utf-8"))


def fetch_caption_and_image(url: str, http) -> tuple[str, str]:
    """Best-effort caption + thumbnail when not pasted in the seed file.

    Tries Instagram oEmbed (needs IG_OEMBED_TOKEN), then the post page's
    OpenGraph description. Returns ("", "") on failure — the caller then relies
    on whatever was pasted, or skips.
    """
    token = os.environ.get("IG_OEMBED_TOKEN")
    if token:
        try:
            import json as _json

            endpoint = (
                "https://graph.facebook.com/v20.0/instagram_oembed"
                f"?url={url}&access_token={token}&fields=author_name,title,thumbnail_url"
            )
            data = _json.loads(http.get(endpoint))
            return (data.get("title") or "").strip(), (data.get("thumbnail_url") or "").strip()
        except Exception:  # noqa: BLE001
            pass
    try:
        html = http.get(url)
        soup = BeautifulSoup(html, "html.parser")
        desc = soup.find("meta", property="og:description")
        image = soup.find("meta", property="og:image")
        return (
            (desc["content"].strip() if desc and desc.get("content") else ""),
            (image["content"].strip() if image and image.get("content") else ""),
        )
    except Exception:  # noqa: BLE001
        return "", ""


def build_recipe(cfg: SourceConfig, entry: SeedEntry, caption: str, image_url: str) -> Recipe | None:
    parsed = caption_mod.parse_caption(caption)
    if not parsed.title and not parsed.ingredients and not parsed.instructions:
        return None
    categories = normalize.split_categories(*(entry.tags or []))
    ingredients = "\n".join(parsed.ingredients)
    instructions = "\n".join(parsed.instructions)
    return Recipe(
        dedup_key=normalize.make_dedup_key(cfg.name, entry.url),
        source=cfg.name,
        source_url=entry.url,
        mode=Mode.REHOST,
        category=cfg.category,
        title=parsed.title,
        text=parsed.description,
        ingredients=ingredients,
        instructions=instructions,
        nutrition=parsed.nutrition,
        yield_=parsed.yield_,
        categories=categories,
        image_url=image_url or entry.image_url,
        author=cfg.rehost_author or "",
        published_at=None,
    )


class InstagramAdapter:
    mode = Mode.REHOST

    def __init__(self, cfg: SourceConfig, http) -> None:
        self.cfg = cfg
        self.http = http
        self.category = cfg.category
        self.name = cfg.name
        self._entries = {
            normalize.canonicalize_url(e.url): e
            for e in load_seed_entries(cfg.seed_file)
            if e.url
        }
        # A recipe is only "confident enough for JSON-LD" when heuristics found
        # both ingredients and steps; build.py reads this to decide emit_jsonld.
        self.confident: dict[str, bool] = {}
        # Instagram carries no reliable publish date; keep the attribute so the
        # adapter matches GenericAdapter's contract (build.py reads date_hints).
        self.date_hints: dict[str, datetime] = {}

    def discover(self) -> list[str]:
        return list(self._entries.keys())

    def fetch_and_parse(self, ref: str) -> Recipe | None:
        entry = self._entries.get(ref) or self._entries.get(normalize.canonicalize_url(ref))
        if entry is None:
            return None
        caption = entry.caption
        image_url = entry.image_url
        if not caption or not image_url:
            fetched_caption, fetched_image = fetch_caption_and_image(entry.url, self.http)
            caption = caption or fetched_caption
            image_url = image_url or fetched_image
        if not caption:
            return None
        recipe = build_recipe(self.cfg, entry, caption, image_url)
        if recipe is not None:
            parsed = caption_mod.parse_caption(caption)
            self.confident[recipe.dedup_key] = parsed.confident
        return recipe
