from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services.rag.image_normalize import (
    is_openai_vision_suffix,
    normalize_image_bytes_for_storage,
    normalize_image_bytes_for_vision,
)


def _png_bytes() -> bytes:
    image = Image.new("RGB", (8, 8), color=(255, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_is_openai_vision_suffix() -> None:
    assert is_openai_vision_suffix("png")
    assert not is_openai_vision_suffix("jp2")


def test_normalize_png_idempotent() -> None:
    raw = _png_bytes()
    converted, mime = normalize_image_bytes_for_vision(raw, suffix_hint="png")
    assert mime == "image/png"
    assert Image.open(io.BytesIO(converted)).format == "PNG"


def test_storage_converts_unknown_suffix_to_png() -> None:
    raw = _png_bytes()
    stored, suffix = normalize_image_bytes_for_storage(raw, suffix="jp2")
    assert suffix == "png"
    assert Image.open(io.BytesIO(stored)).format == "PNG"


def test_empty_payload_raises() -> None:
    with pytest.raises(ValueError):
        normalize_image_bytes_for_vision(b"")
