from pathlib import Path

from melarss import images

from conftest import tiny_png


class OneShotHttp:
    def __init__(self, payload: bytes):
        self.payload = payload

    def get_bytes(self, url):
        return self.payload


def test_self_host_real_image(tmp_path):
    dest = tmp_path / "img" / "x.jpg"
    ok = images.self_host_image("https://host/x.png", dest, OneShotHttp(tiny_png()))
    assert ok is True
    assert dest.exists() and dest.stat().st_size > 0


def test_self_host_rejects_non_image(tmp_path):
    dest = tmp_path / "img" / "x.jpg"
    # e.g. an HTML error page served where an image was expected
    ok = images.self_host_image("https://host/x.png", dest, OneShotHttp(b"<html>nope</html>"))
    assert ok is False
    assert not dest.exists()


def test_base64_rejects_non_image():
    assert images.image_to_base64("https://host/x.png", OneShotHttp(b"not an image")) is None
