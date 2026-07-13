"""Load and validate sources.yaml into typed SourceConfig objects.

Adding a source is normally just a YAML entry — no code — unless it needs a
bespoke discovery hook (Instagram, Mob chef page).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import Category, Mode

VALID_DISCOVERY = {"sitemap", "native_feed", "chef_page", "instagram"}


@dataclass
class SourceConfig:
    name: str
    mode: Mode
    discovery: str
    category: Category = Category.RECIPE
    enabled: bool = True

    # discovery inputs (one is used depending on `discovery`)
    sitemap: str | None = None
    feed_url: str | None = None
    url: str | None = None
    seed_file: str | None = None
    url_pattern: str | None = None

    # extraction / rendering
    scraper_host: str | None = None
    rehost_author: str | None = None

    # limits / politeness
    backfill_limit: int | None = None
    max_new_per_run: int = 20
    request_delay_seconds: float = 2.0
    user_agent: str | None = None

    def title(self) -> str:
        return self.name.replace("-", " ").replace("_", " ").title()


@dataclass
class Config:
    sources: list[SourceConfig] = field(default_factory=list)

    def enabled(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


def _coerce_source(name: str, raw: dict, defaults: dict) -> SourceConfig:
    merged = {**defaults, **raw}
    try:
        mode = Mode(merged["mode"])
    except KeyError as exc:
        raise ValueError(f"source '{name}': missing required field 'mode'") from exc
    except ValueError as exc:
        raise ValueError(f"source '{name}': invalid mode '{merged.get('mode')}'") from exc

    discovery = merged.get("discovery")
    if discovery not in VALID_DISCOVERY:
        raise ValueError(
            f"source '{name}': invalid/missing discovery '{discovery}' "
            f"(expected one of {sorted(VALID_DISCOVERY)})"
        )
    category = Category(merged.get("category", "recipe"))

    return SourceConfig(
        name=name,
        mode=mode,
        discovery=discovery,
        category=category,
        enabled=bool(merged.get("enabled", True)),
        sitemap=merged.get("sitemap"),
        feed_url=merged.get("feed_url"),
        url=merged.get("url"),
        seed_file=merged.get("seed_file"),
        url_pattern=merged.get("url_pattern"),
        scraper_host=merged.get("scraper_host"),
        rehost_author=merged.get("rehost_author"),
        backfill_limit=merged.get("backfill_limit"),
        max_new_per_run=int(merged.get("max_new_per_run", 20)),
        request_delay_seconds=float(merged.get("request_delay_seconds", 2.0)),
        user_agent=merged.get("user_agent"),
    )


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults", {}) or {}
    sources = []
    seen: set[str] = set()
    for entry in data.get("sources", []) or []:
        name = entry.get("name")
        if not name:
            raise ValueError("every source needs a 'name'")
        if name in seen:
            raise ValueError(f"duplicate source name '{name}'")
        seen.add(name)
        sources.append(_coerce_source(name, entry, defaults))
    return Config(sources=sources)
