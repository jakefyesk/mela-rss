"""MindLink pull adapter: REST list+detail -> rehost Recipe tagged 'MindLink'."""

from __future__ import annotations

import json

import pytest

from melarss.config import SourceConfig
from melarss.discovery.mindlink import MindLinkAdapter, _safe_link
from melarss.models import Category, Mode

BASE = "https://mind-link.example"


def _list_url(cursor: str | None = None) -> str:
    url = f"{BASE}/api/v1/items?type=recipe&limit=50"
    return f"{url}&cursor={cursor}" if cursor else url


def _detail_url(item_id: str) -> str:
    return f"{BASE}/api/v1/items/{item_id}"


class MindLinkFake:
    """Serves canned JSON and asserts the bearer token is always sent."""

    def __init__(self, routes: dict[str, str]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, headers: dict | None = None) -> str:
        assert headers and headers.get("Authorization") == "Bearer mlk_test", headers
        self.calls.append(url)
        if url not in self.routes:
            raise RuntimeError(f"MindLinkFake: no route for {url}")
        return self.routes[url]


def _cfg() -> SourceConfig:
    return SourceConfig(
        name="mindlink",
        mode=Mode.REHOST,
        discovery="mindlink",
        category=Category.RECIPE,
        url=BASE,  # base may also come from MINDLINK_API_URL env
    )


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("MINDLINK_TOKEN", "mlk_test")
    monkeypatch.delenv("MINDLINK_API_URL", raising=False)


ONE_ITEM = {
    "items": [
        {
            "id": "abc-123",
            "title": "Crispy Gochujang Tofu",
            "url": "https://www.instagram.com/p/XYZ/",
            "tags": ["tofu", "korean"],
            "created_at": "2026-07-19T10:00:00Z",
            "enriched_at": "2026-07-19T10:05:00Z",
        }
    ],
    "next_cursor": None,
}

DETAIL = {
    "id": "abc-123",
    "type": "recipe",
    "title": "Crispy Gochujang Tofu",
    "summary": "A fast, crispy weeknight tofu.",
    "url": "https://www.instagram.com/p/XYZ/",
    "tags": ["tofu", "korean"],
    "ocr_text": (
        "Crispy Gochujang Tofu\n"
        "Ingredients:\n"
        "- 400g firm tofu\n"
        "- 2 tbsp gochujang\n"
        "Method:\n"
        "1. Press and cube the tofu.\n"
        "2. Fry until crisp, then toss in gochujang.\n"
    ),
    "metadata": {"author": "@somechef"},
    "media": [
        {"kind": "thumbnail", "mime_type": "image/webp", "url": "https://storage/signed/thumb.webp"}
    ],
}


def test_discover_and_parse_full_recipe():
    fake = MindLinkFake({_list_url(): json.dumps(ONE_ITEM), _detail_url("abc-123"): json.dumps(DETAIL)})
    ad = MindLinkAdapter(_cfg(), fake)

    refs = ad.discover()
    assert refs == ["mindlink://item/abc-123"]
    # discovery is one cheap list call; no detail fetch yet
    assert fake.calls == [_list_url()]
    # publish date comes from the enrich time
    assert ad.date_hints[refs[0]].year == 2026

    recipe = ad.fetch_and_parse(refs[0])
    assert recipe is not None
    assert recipe.mode == Mode.REHOST
    assert recipe.saved_via == "MindLink"
    assert recipe.title == "Crispy Gochujang Tofu"
    assert "400g firm tofu" in recipe.ingredients
    assert "Press and cube the tofu." in recipe.instructions
    assert recipe.image_url == "https://storage/signed/thumb.webp"
    assert recipe.source_url == "https://www.instagram.com/p/XYZ/"
    assert recipe.author == "@somechef"
    # the marker leads the Mela categories, then the user's tags
    assert recipe.mela_categories() == ["MindLink", "tofu", "korean"]
    # both ingredients and steps found -> confident (build emits JSON-LD)
    assert ad.confident[recipe.dedup_key] is True


def test_unconfigured_is_a_graceful_noop(monkeypatch):
    monkeypatch.delenv("MINDLINK_TOKEN", raising=False)
    cfg = SourceConfig(name="mindlink", mode=Mode.REHOST, discovery="mindlink", url=BASE)
    ad = MindLinkAdapter(cfg, MindLinkFake({}))
    assert ad.discover() == []


def test_missing_base_url_is_a_graceful_noop(monkeypatch):
    # token present, but neither cfg.url nor MINDLINK_API_URL set
    cfg = SourceConfig(name="mindlink", mode=Mode.REHOST, discovery="mindlink")
    ad = MindLinkAdapter(cfg, MindLinkFake({}))
    assert ad.discover() == []


def test_base_url_from_env(monkeypatch):
    monkeypatch.setenv("MINDLINK_API_URL", BASE)
    cfg = SourceConfig(name="mindlink", mode=Mode.REHOST, discovery="mindlink")  # no cfg.url
    fake = MindLinkFake({_list_url(): json.dumps(ONE_ITEM)})
    ad = MindLinkAdapter(cfg, fake)
    assert ad.discover() == ["mindlink://item/abc-123"]


def test_pagination_follows_cursor():
    page1 = {"items": [{"id": "a"}, {"id": "b"}], "next_cursor": "CUR2"}
    page2 = {"items": [{"id": "c"}], "next_cursor": None}
    fake = MindLinkFake({_list_url(): json.dumps(page1), _list_url("CUR2"): json.dumps(page2)})
    ad = MindLinkAdapter(_cfg(), fake)
    refs = ad.discover()
    assert refs == [f"mindlink://item/{i}" for i in ("a", "b", "c")]


def test_detail_fetch_failure_degrades_to_list_row():
    # detail endpoint is missing -> adapter falls back to the list projection and
    # still produces a page (title + no body -> unconfident, Mela's ML reads it).
    fake = MindLinkFake({_list_url(): json.dumps(ONE_ITEM)})  # no detail route
    ad = MindLinkAdapter(_cfg(), fake)
    refs = ad.discover()
    recipe = ad.fetch_and_parse(refs[0])
    assert recipe is not None
    assert recipe.title == "Crispy Gochujang Tofu"
    assert recipe.saved_via == "MindLink"
    assert ad.confident[recipe.dedup_key] is False


def test_dedup_key_is_stable_across_runs():
    fake = MindLinkFake({_list_url(): json.dumps(ONE_ITEM), _detail_url("abc-123"): json.dumps(DETAIL)})
    a = MindLinkAdapter(_cfg(), fake)
    a.discover()
    r1 = a.fetch_and_parse("mindlink://item/abc-123")
    b = MindLinkAdapter(_cfg(), fake)
    b.discover()
    r2 = b.fetch_and_parse("mindlink://item/abc-123")
    assert r1.dedup_key == r2.dedup_key


def test_unsafe_source_link_is_dropped():
    assert _safe_link("javascript:alert(1)") == ""
    assert _safe_link("data:text/html,x") == ""
    assert _safe_link("https://ok.example/p/1") == "https://ok.example/p/1"
    assert _safe_link("HTTP://ok.example") == "HTTP://ok.example"

    item = dict(DETAIL, url="javascript:alert(1)")
    fake = MindLinkFake({_list_url(): json.dumps({"items": [item], "next_cursor": None}), _detail_url("abc-123"): json.dumps(item)})
    ad = MindLinkAdapter(_cfg(), fake)
    ad.discover()
    recipe = ad.fetch_and_parse("mindlink://item/abc-123")
    assert recipe.source_url == ""  # scheme refused
