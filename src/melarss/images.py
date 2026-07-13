"""Image handling: self-host (for rehost sources) and base64 (for bundles).

Ensuring every recipe carries an image is a first-class requirement. For rehost
sources we download the source image once, downscale it, and save it under
docs/ so the URL is stable and Mela always finds it (IG CDN URLs expire).
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

MAX_DIM = 1600
JPEG_QUALITY = 80


def _downscale(raw: bytes) -> bytes:
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:  # noqa: BLE001 — not a decodable image; keep original bytes
        return raw
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > MAX_DIM:
        img.thumbnail((MAX_DIM, MAX_DIM))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


def self_host_image(image_url: str, dest: Path, http) -> bool:
    """Download + downscale `image_url` to `dest`. Returns True on success."""
    if not image_url:
        return False
    try:
        raw = http.get_bytes(image_url)
    except Exception:  # noqa: BLE001
        return False
    data = _downscale(raw)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


def image_to_base64(image_url: str, http) -> str | None:
    if not image_url:
        return None
    try:
        raw = http.get_bytes(image_url)
    except Exception:  # noqa: BLE001
        return None
    return base64.b64encode(_downscale(raw)).decode("ascii")
