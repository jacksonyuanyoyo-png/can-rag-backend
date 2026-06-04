from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.domain.knowledge_base import SearchHit
from app.services.markdown_render import (
    BACKEND_URL_PLACEHOLDER,
    markdown_payload_for_storage_text,
    upload_asset_path,
)
from app.services.rag.parsing.md_parser import extract_image_storage_keys

if TYPE_CHECKING:
    from app.services.knowledge_base_service import KnowledgeBaseService


def chunk_view_path(*, kb_id: str, file_id: str, chunk_id: str) -> str:
    return f"/v1/knowledge-bases/{kb_id}/files/{file_id}/chunks/{chunk_id}"


def file_view_path(*, kb_id: str, file_id: str) -> str:
    return f"/v1/knowledge-bases/{kb_id}/files/{file_id}"


def citation_from_hit(hit: SearchHit, *, index: int) -> dict[str, Any]:
    """SSE / 消息 API 使用的引用项（camelCase）。"""
    raw = hit.citation or {}
    kb_id = str(raw.get("kb_id") or "")
    chunk_id = hit.chunk_id
    snippet = hit.text
    image_keys = list(raw.get("storage_keys") or [])
    if not image_keys:
        primary = raw.get("storage_key")
        if primary:
            image_keys = [str(primary)]
    if not image_keys:
        image_keys = extract_image_storage_keys(snippet)

    payload: dict[str, Any] = {
        "index": index,
        "kbId": kb_id or None,
        "fileId": hit.document_id,
        "chunkId": chunk_id,
        "page": raw.get("page"),
        "chunkIndex": raw.get("chunk_index"),
        "score": hit.score,
        "snippet": snippet,
        "charCount": len(snippet),
        "fileName": hit.file_name,
        "type": raw.get("type", "text"),
    }
    if image_keys:
        payload["storageKey"] = image_keys[0]
        payload["imageKeys"] = image_keys
        payload["imageAssets"] = [
            {"storageKey": key, "assetUrl": upload_asset_path(key)} for key in image_keys
        ]
    if kb_id:
        payload["chunkViewPath"] = chunk_view_path(
            kb_id=kb_id,
            file_id=hit.document_id,
            chunk_id=chunk_id,
        )
        payload["fileViewPath"] = file_view_path(kb_id=kb_id, file_id=hit.document_id)
    payload.update(markdown_payload_for_storage_text(snippet))
    return payload


def citations_from_hits(hits: list[SearchHit]) -> list[dict[str, Any]]:
    return [citation_from_hit(hit, index=index) for index, hit in enumerate(hits, start=1)]


def build_message_sources(
    citations: list[dict[str, Any]],
    *,
    kb_service: KnowledgeBaseService | None,
) -> dict[str, Any]:
    """回答完成后供前端展示：知识分段列表 + 来源文件元数据 + 图示索引。"""
    segments: list[dict[str, Any]] = []
    for citation in citations:
        segments.append(
            {
                "index": citation["index"],
                "kbId": citation.get("kbId"),
                "fileId": citation.get("fileId"),
                "chunkId": citation.get("chunkId"),
                "type": citation.get("type", "text"),
                "score": citation.get("score"),
                "snippet": citation.get("snippet"),
                "charCount": citation.get("charCount"),
                "page": citation.get("page"),
                "chunkIndex": citation.get("chunkIndex"),
                "fileName": citation.get("fileName"),
                "storageKey": citation.get("storageKey"),
                "imageKeys": citation.get("imageKeys"),
                "imageAssets": citation.get("imageAssets"),
                "chunkViewPath": citation.get("chunkViewPath"),
                "fileViewPath": citation.get("fileViewPath"),
                "textFormat": citation.get("textFormat", "markdown"),
                "markdown": citation.get("markdown", citation.get("snippet", "")),
                "hasImages": citation.get("hasImages", False),
            }
        )

    files_by_id: dict[str, dict[str, Any]] = {}
    for citation in citations:
        file_id = citation.get("fileId")
        kb_id = citation.get("kbId")
        if not file_id or not kb_id:
            continue
        key = f"{kb_id}:{file_id}"
        if key not in files_by_id:
            file_meta: dict[str, Any] = {
                "kbId": kb_id,
                "fileId": file_id,
                "fileName": citation.get("fileName"),
                "fileViewPath": citation.get("fileViewPath")
                or file_view_path(kb_id=str(kb_id), file_id=str(file_id)),
                "segmentIndexes": [],
            }
            record = _lookup_kb_file(kb_service, str(file_id))
            if record is not None:
                file_meta.update(
                    {
                        "fileName": record.file_name,
                        "mimeType": record.mime_type,
                        "sizeBytes": record.size_bytes,
                        "storageKey": record.storage_key,
                        "format": record.file_format
                        or _guess_format(record.file_name),
                        "uploadedAt": record.created_at.isoformat().replace(
                            "+00:00", "Z"
                        ),
                        "status": record.status,
                    }
                )
                if record.storage_key:
                    file_meta["fileAssetUrl"] = upload_asset_path(record.storage_key)
            files_by_id[key] = file_meta
        files_by_id[key]["segmentIndexes"].append(citation["index"])

    figures: list[dict[str, Any]] = []
    seen_figure_keys: set[str] = set()
    for citation in citations:
        ref = int(citation["index"])
        for asset in citation.get("imageAssets") or []:
            storage_key = str(asset.get("storageKey", ""))
            if not storage_key or storage_key in seen_figure_keys:
                continue
            seen_figure_keys.add(storage_key)
            figures.append(
                {
                    "ref": ref,
                    "storageKey": storage_key,
                    "assetUrl": asset.get("assetUrl") or upload_asset_path(storage_key),
                    "fileId": citation.get("fileId"),
                    "kbId": citation.get("kbId"),
                    "chunkId": citation.get("chunkId"),
                }
            )

    return {
        "segments": segments,
        "files": list(files_by_id.values()),
        "figures": figures,
        "render": {
            "assistantContent": "markdown",
            "segmentContent": "markdown",
            "imagePathPrefix": "/v1/uploads/assets/",
            "backendUrlPlaceholder": BACKEND_URL_PLACEHOLDER,
        },
    }


def _lookup_kb_file(
    kb_service: KnowledgeBaseService | None,
    file_id: str,
) -> Any | None:
    if kb_service is None:
        return None
    repo = getattr(kb_service, "_upload_repository", None)
    if repo is None:
        return None
    return repo.get_kb_file(file_id)


def _guess_format(file_name: str) -> str:
    if "." not in file_name:
        return "unknown"
    return file_name.rsplit(".", 1)[-1].lower()
