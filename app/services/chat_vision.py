from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.services.rag.image_normalize import normalize_image_bytes_for_vision
from app.services.rag.pipeline import _guess_mime

logger = logging.getLogger(__name__)


def image_bytes_to_data_url(image_bytes: bytes, *, mime_type: str) -> str:
    encoded = base64.standard_b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def load_citation_figure_data_urls(
    citations: list[dict[str, Any]],
    *,
    upload_root: Path,
    max_images: int,
) -> list[tuple[int, str, str]]:
    """加载 citation 中的图示，返回 (来源编号, storage_key, data_url)。"""
    figures: list[tuple[int, str, str]] = []
    seen_keys: set[str] = set()
    for citation in citations:
        if len(figures) >= max_images:
            break
        storage_key = citation.get("storageKey")
        if not storage_key or storage_key in seen_keys:
            continue
        relative = Path(str(storage_key))
        if relative.is_absolute() or ".." in relative.parts:
            continue
        path = upload_root / relative
        if not path.is_file():
            logger.debug("citation image missing: %s", storage_key)
            continue
        try:
            image_bytes = path.read_bytes()
        except OSError:
            logger.debug("citation image read failed: %s", storage_key)
            continue
        suffix = storage_key.rsplit(".", 1)[-1] if "." in storage_key else "png"
        try:
            image_bytes, mime = normalize_image_bytes_for_vision(
                image_bytes,
                suffix_hint=suffix,
            )
        except (ValueError, RuntimeError):
            logger.debug("citation image normalize failed: %s", storage_key)
            continue
        data_url = image_bytes_to_data_url(image_bytes, mime_type=mime)
        seen_keys.add(storage_key)
        figures.append((int(citation["index"]), storage_key, data_url))
    return figures


def append_citation_figures_to_messages(
    messages: list[dict[str, Any]],
    *,
    citations: list[dict[str, Any]],
    upload_root: Path,
    settings: Settings,
) -> list[dict[str, Any]]:
    """在发往 Chat API 前，将检索到的图示以 vision 多模态形式注入上下文。"""
    if not settings.CHAT_VISION_ENABLED or not citations:
        return messages
    figures = load_citation_figure_data_urls(
        citations,
        upload_root=upload_root,
        max_images=settings.CHAT_VISION_MAX_IMAGES,
    )
    if not figures:
        return messages

    content_parts: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "以下是与检索来源对应的页面/文档图示（与上文来源编号一致），"
                "回答时请结合文字与图示，并在引用处标注 [n]，可提示用户查看图[n]。"
            ),
        }
    ]
    for index, storage_key, data_url in figures:
        content_parts.append(
            {
                "type": "text",
                "text": f"图[{index}]（{storage_key}）",
            }
        )
        content_parts.append(
            {"type": "image_url", "image_url": {"url": data_url, "detail": "auto"}}
        )

    enriched = list(messages)
    insert_at = len(enriched)
    for position, message in enumerate(enriched):
        if message.get("role") == "user":
            insert_at = position
    enriched.insert(insert_at, {"role": "user", "content": content_parts})
    return enriched
