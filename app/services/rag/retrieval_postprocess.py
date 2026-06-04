from __future__ import annotations

from app.domain.knowledge_base import SearchHit
from app.services.rag.vlm_text_quality import is_refusal_like_vlm_text

_TEXT_CHUNK_TYPE = "text"
_IMAGE_CHUNK_TYPE = "image"


def _chunk_type(hit: SearchHit) -> str:
    citation = hit.citation or {}
    return str(citation.get("type") or _TEXT_CHUNK_TYPE)


def _storage_keys(hit: SearchHit) -> list[str]:
    citation = hit.citation or {}
    keys: list[str] = []
    primary = citation.get("storage_key")
    if primary:
        keys.append(str(primary))
    for item in citation.get("storage_keys") or []:
        key = str(item)
        if key and key not in keys:
            keys.append(key)
    return keys


def _drop_standalone_image_hits_when_text_exists(hits: list[SearchHit]) -> list[SearchHit]:
    """正文 text 段已含 Markdown 图片时，不再返回独立 type=image 的 VLM 描述段。"""
    if not hits:
        return hits
    has_text = any(_chunk_type(hit) == _TEXT_CHUNK_TYPE for hit in hits)
    if not has_text:
        return hits
    return [hit for hit in hits if _chunk_type(hit) != _IMAGE_CHUNK_TYPE]


def is_low_quality_image_hit(hit: SearchHit) -> bool:
    if _chunk_type(hit) != _IMAGE_CHUNK_TYPE:
        return False
    return is_refusal_like_vlm_text(hit.text)


def _type_priority(chunk_type: str) -> int:
    return 0 if chunk_type == _TEXT_CHUNK_TYPE else 1


def postprocess_search_hits(
    hits: list[SearchHit],
    *,
    top_k: int,
) -> list[SearchHit]:
    """过滤低质量图示分段，并按 storage_key 去重（优先保留 text 段）。"""
    if top_k <= 0:
        return []

    filtered: list[SearchHit] = []
    for hit in hits:
        if is_low_quality_image_hit(hit):
            continue
        filtered.append(hit)

    filtered = _drop_standalone_image_hits_when_text_exists(filtered)

    filtered.sort(key=lambda item: item.score, reverse=True)

    selected: list[SearchHit] = []
    seen_keys: set[str] = set()
    deferred_image: list[SearchHit] = []

    for hit in filtered:
        keys = _storage_keys(hit)
        if not keys:
            selected.append(hit)
            continue
        if any(key in seen_keys for key in keys):
            chunk_type = _chunk_type(hit)
            if chunk_type == _IMAGE_CHUNK_TYPE:
                continue
            # text 段覆盖同 key 的 image 段：移除已选中的 image 再插入
            selected = [
                item
                for item in selected
                if not (
                    _chunk_type(item) == _IMAGE_CHUNK_TYPE
                    and any(k in _storage_keys(item) for k in keys)
                )
            ]
            seen_keys.update(keys)
            selected.append(hit)
            continue

        if _chunk_type(hit) == _IMAGE_CHUNK_TYPE:
            deferred_image.append(hit)
            continue

        seen_keys.update(keys)
        selected.append(hit)

    for hit in deferred_image:
        keys = _storage_keys(hit)
        if keys and any(key in seen_keys for key in keys):
            continue
        if keys:
            seen_keys.update(keys)
        selected.append(hit)

    selected.sort(key=lambda item: item.score, reverse=True)
    return selected[:top_k]
