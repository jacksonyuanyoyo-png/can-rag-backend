from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

OPENAI_VISION_SUFFIXES = frozenset({"png", "jpg", "jpeg", "gif", "webp"})

_SUFFIX_TO_MIME: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def vision_mime_for_suffix(suffix: str) -> str:
    normalized = suffix.lstrip(".").lower()
    if normalized == "jpg":
        normalized = "jpeg"
    return _SUFFIX_TO_MIME.get(normalized, "image/png")


def is_openai_vision_suffix(suffix: str) -> bool:
    normalized = suffix.lstrip(".").lower()
    return normalized in OPENAI_VISION_SUFFIXES


def normalize_image_bytes_for_vision(
    data: bytes,
    *,
    suffix_hint: str | None = None,
) -> tuple[bytes, str]:
    """将任意位图转为 OpenAI Vision 支持的 PNG（png/jpeg/gif/webp）。"""
    if not data:
        raise ValueError("empty image payload")

    suffix = (suffix_hint or "").lstrip(".").lower()
    if suffix == "jpg":
        suffix = "jpeg"

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "图片转码需要 Pillow，请安装: pip install Pillow"
        ) from exc

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise ValueError(f"无法解码图片（suffix={suffix_hint}）") from exc

    fmt = (image.format or "").upper()
    if fmt in {"PNG", "JPEG", "GIF", "WEBP"} and suffix in OPENAI_VISION_SUFFIXES:
        buffer = io.BytesIO()
        if fmt == "PNG":
            image.save(buffer, format="PNG")
            return buffer.getvalue(), "image/png"
        if fmt == "JPEG":
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.save(buffer, format="JPEG", quality=92)
            return buffer.getvalue(), "image/jpeg"
        if fmt == "GIF":
            image.save(buffer, format="GIF")
            return buffer.getvalue(), "image/gif"
        if fmt == "WEBP":
            image.save(buffer, format="WEBP")
            return buffer.getvalue(), "image/webp"

    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGB")
    elif image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), "image/png"


def normalize_image_bytes_for_storage(data: bytes, *, suffix: str) -> tuple[bytes, str]:
    """落盘前将 jp2/tiff 等转为 png，避免后续 Vision / 静态资源不兼容。"""
    if is_openai_vision_suffix(suffix):
        return data, suffix.lstrip(".").lower()
    try:
        png_bytes, _ = normalize_image_bytes_for_vision(data, suffix_hint=suffix)
    except ValueError:
        logger.warning("图片转 PNG 失败，仍按原格式落盘: suffix=%s", suffix)
        return data, suffix.lstrip(".").lower() or "bin"
    return png_bytes, "png"
