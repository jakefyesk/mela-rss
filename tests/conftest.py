"""Shared test helpers: a fake HTTP client and fixture access."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def tiny_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 120, 60)).save(buf, format="PNG")
    return buf.getvalue()


class FakeHttp:
    """Serves canned responses from a routing table. Ignores constructor kwargs
    so it can stand in for melarss.http.Http."""

    ROUTES: dict[str, str] = {}
    BINARY_ROUTES: dict[str, bytes] = {}
    DEFAULT_IMAGE: bytes | None = None

    def __init__(self, *args, **kwargs) -> None:  # noqa: D401 - drop-in for Http
        self.session = type("S", (), {"headers": {"User-Agent": "fake"}})()

    def get(self, url: str, headers: dict | None = None) -> str:
        if url in self.ROUTES:
            return self.ROUTES[url]
        raise RuntimeError(f"FakeHttp: no route for {url}")

    def get_bytes(self, url: str, headers: dict | None = None) -> bytes:
        if url in self.BINARY_ROUTES:
            return self.BINARY_ROUTES[url]
        if self.DEFAULT_IMAGE is not None:
            return self.DEFAULT_IMAGE
        raise RuntimeError(f"FakeHttp: no binary route for {url}")


@pytest.fixture
def fake_http_cls():
    """Return a fresh FakeHttp subclass with isolated routing tables."""

    class _Fake(FakeHttp):
        ROUTES: dict[str, str] = {}
        BINARY_ROUTES: dict[str, bytes] = {}
        DEFAULT_IMAGE = tiny_png()

    return _Fake
