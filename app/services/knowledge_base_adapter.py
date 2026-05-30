from __future__ import annotations

from typing import Literal

from app.core.config import get_settings
from app.domain.knowledge_base import (
    EMBEDDING_MODEL_ID_KEY,
    KnowledgeBaseMetadata,
    RESOURCE_TYPE_KEY,
)
from app.domain.model_catalog import default_embedding_model_id
from app.services.knowledge_base_service import KnowledgeBaseService

ResourceType = Literal["personal", "team"]

DEFAULT_RESOURCE_TYPE: ResourceType = "personal"


class KnowledgeBaseNotFoundError(Exception):
    pass


class KnowledgeBaseDuplicateError(Exception):
    pass


def get_resource_type(metadata: KnowledgeBaseMetadata) -> ResourceType:
    raw = metadata.backend_refs.get(RESOURCE_TYPE_KEY, DEFAULT_RESOURCE_TYPE)
    if raw in ("personal", "team"):
        return raw
    return DEFAULT_RESOURCE_TYPE


def to_api_dict(
    metadata: KnowledgeBaseMetadata,
    *,
    file_count: int | None = None,
) -> dict[str, object]:
    return {
        "id": metadata.id,
        "name": metadata.name,
        "description": metadata.description,
        "fileCount": file_count if file_count is not None else len(metadata.documents),
        "resourceType": get_resource_type(metadata),
        "updatedAt": metadata.updated_at,
    }


def find_kb(service: KnowledgeBaseService, kb_id: str) -> KnowledgeBaseMetadata | None:
    return service.find_kb_by_id(kb_id)


def require_kb(service: KnowledgeBaseService, kb_id: str) -> KnowledgeBaseMetadata:
    metadata = find_kb(service, kb_id)
    if metadata is None:
        raise KnowledgeBaseNotFoundError("Knowledge base not found")
    return metadata


def list_kbs_paginated(
    service: KnowledgeBaseService,
    *,
    page: int,
    page_size: int,
    q: str | None = None,
    scope: str | None = None,
) -> tuple[list[KnowledgeBaseMetadata], int]:
    items = service.list_kbs()

    if q:
        needle = q.casefold()
        items = [
            kb
            for kb in items
            if needle in kb.name.casefold() or needle in kb.description.casefold()
        ]

    if scope in ("personal", "team"):
        items = [kb for kb in items if get_resource_type(kb) == scope]

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end], total


def create_kb(
    service: KnowledgeBaseService,
    *,
    name: str,
    description: str = "",
    embedding_model_id: str | None = None,
    resource_type: ResourceType = DEFAULT_RESOURCE_TYPE,
) -> KnowledgeBaseMetadata:
    try:
        metadata = service.create_kb(name=name, description=description)
    except ValueError as exc:
        if "知识库已存在" in str(exc):
            raise KnowledgeBaseDuplicateError("Knowledge base name already exists") from exc
        raise

    metadata.backend_refs[RESOURCE_TYPE_KEY] = resource_type
    effective_embedding = embedding_model_id or default_embedding_model_id(
        get_settings().OPENAI_EMBEDDING_MODEL
    )
    metadata.backend_refs[EMBEDDING_MODEL_ID_KEY] = effective_embedding
    return service.save_kb(metadata)


def delete_kb(service: KnowledgeBaseService, kb_id: str) -> None:
    metadata = require_kb(service, kb_id)
    try:
        service.delete_kb(metadata.name)
    except ValueError as exc:
        if "知识库不存在" in str(exc):
            raise KnowledgeBaseNotFoundError("Knowledge base not found") from exc
        raise
