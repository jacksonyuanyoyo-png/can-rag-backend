from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import FileResponse

from app.api.common import get_request_id, paginated_response, success_response
from app.api.schemas.knowledge_base import (
    BatchDeleteFilesRequest,
    DeleteFileResponse,
    DeleteKnowledgeBaseResponse,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseUpdateRequest,
)
from app.core.errors import BusinessError, ErrorCode
from app.domain.model_catalog import default_embedding_model_id, is_valid_embedding_model_id
from app.services.kb_source_file import content_disposition_header
from app.services.knowledge_base_adapter import (
    KnowledgeBaseDuplicateError,
    KnowledgeBaseNotFoundError,
    create_kb,
    delete_kb,
    list_kbs_paginated,
    require_kb,
)
from app.services.knowledge_base_service import KnowledgeBaseService

knowledge_base_router = APIRouter(prefix="/v1/knowledge-bases", tags=["Knowledge Bases"])


def _kb_service(request: Request) -> KnowledgeBaseService:
    service = getattr(request.app.state, "knowledge_base_service", None)
    if service is None:
        raise RuntimeError("KnowledgeBaseService is not initialized")
    return service


def _raise_kb_not_found(exc: KnowledgeBaseNotFoundError) -> None:
    raise BusinessError(ErrorCode.KB_NOT_FOUND, str(exc)) from exc


def _raise_kb_duplicate(exc: KnowledgeBaseDuplicateError) -> None:
    raise BusinessError(ErrorCode.KB_NAME_DUPLICATED, str(exc)) from exc


@knowledge_base_router.get("")
async def list_knowledge_bases(
    request: Request,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    q: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    service = _kb_service(request)
    items, total = list_kbs_paginated(
        service,
        page=page,
        page_size=pageSize,
        q=q,
        scope=scope,
    )
    return paginated_response(
        data=[service.to_api_dict(item) for item in items],
        page=page,
        page_size=pageSize,
        total=total,
        request_id=get_request_id(request),
    )


@knowledge_base_router.post("", status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    request: Request,
    body: KnowledgeBaseCreateRequest,
) -> dict[str, Any]:
    service = _kb_service(request)
    settings = request.app.state.settings
    effective_embedding_model_id = body.embedding_model_id or default_embedding_model_id(
        settings.OPENAI_EMBEDDING_MODEL
    )
    if not is_valid_embedding_model_id(effective_embedding_model_id):
        raise BusinessError(
            ErrorCode.VALIDATION_ERROR,
            message="embeddingModelId 不是支持的向量模型",
            details={"embeddingModelId": body.embedding_model_id},
        )
    try:
        metadata = create_kb(
            service,
            name=body.name,
            description=body.description,
            embedding_model_id=effective_embedding_model_id,
        )
    except KnowledgeBaseDuplicateError as exc:
        _raise_kb_duplicate(exc)
    return success_response(
        data=service.to_api_dict(metadata),
        request_id=get_request_id(request),
    )


@knowledge_base_router.patch("/{kb_id}")
async def update_knowledge_base(
    request: Request,
    kb_id: str,
    body: KnowledgeBaseUpdateRequest,
) -> dict[str, Any]:
    service = _kb_service(request)
    try:
        metadata = require_kb(service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        _raise_kb_not_found(exc)

    updated = service.update_kb(
        metadata=metadata,
        name=body.name,
        description=body.description,
        resource_type=body.resource_type,
    )
    return success_response(
        data=service.to_api_dict(updated),
        request_id=get_request_id(request),
    )


@knowledge_base_router.get("/{kb_id}")
async def get_knowledge_base(request: Request, kb_id: str) -> dict[str, Any]:
    service = _kb_service(request)
    try:
        metadata = require_kb(service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        _raise_kb_not_found(exc)
    return success_response(
        data=service.to_api_dict(metadata),
        request_id=get_request_id(request),
    )


@knowledge_base_router.delete("/{kb_id}")
async def delete_knowledge_base(request: Request, kb_id: str) -> dict[str, Any]:
    service = _kb_service(request)
    try:
        delete_kb(service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        _raise_kb_not_found(exc)
    return success_response(
        data=DeleteKnowledgeBaseResponse().model_dump(),
        request_id=get_request_id(request),
    )


@knowledge_base_router.get("/{kb_id}/files")
async def list_knowledge_base_files(
    request: Request,
    kb_id: str,
    page: int = Query(1, ge=1),
    pageSize: int = Query(10, ge=1, le=100),
    q: str | None = None,
    status: str | None = None,
    format: str | None = Query(default=None),
) -> dict[str, Any]:
    service = _kb_service(request)
    try:
        metadata = require_kb(service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        _raise_kb_not_found(exc)

    result = service.list_files(
        metadata=metadata,
        page=page,
        page_size=pageSize,
        q=q,
        status=status,
        file_format=format,
    )
    return paginated_response(
        data=[item.to_api_dict() for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
        request_id=get_request_id(request),
    )


@knowledge_base_router.get("/{kb_id}/files/{file_id}/chunks")
async def list_knowledge_base_file_chunks(
    request: Request,
    kb_id: str,
    file_id: str,
    page: int = Query(1, ge=1),
    pageSize: int = Query(10, ge=1, le=100),
    q: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    service = _kb_service(request)
    try:
        require_kb(service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        _raise_kb_not_found(exc)

    items, total = service.list_file_chunks(
        kb_id=kb_id,
        file_id=file_id,
        page=page,
        page_size=pageSize,
        q=q,
        status=status,
    )
    return paginated_response(
        data=[item.to_api_dict() for item in items],
        page=page,
        page_size=pageSize,
        total=total,
        request_id=get_request_id(request),
    )


@knowledge_base_router.get("/{kb_id}/files/{file_id}/chunks/{data_id}")
async def get_knowledge_base_file_chunk_with_context(
    request: Request,
    kb_id: str,
    file_id: str,
    data_id: str,
    context: int = Query(1, ge=0, le=10),
) -> dict[str, Any]:
    service = _kb_service(request)
    try:
        require_kb(service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        _raise_kb_not_found(exc)

    result = service.get_file_chunk_with_context(
        kb_id=kb_id,
        file_id=file_id,
        data_id=data_id,
        context=context,
    )
    return success_response(
        data=result.to_api_dict(),
        request_id=get_request_id(request),
    )


@knowledge_base_router.get("/{kb_id}/files/{file_id}/raw")
async def get_knowledge_base_file_raw(
    request: Request,
    kb_id: str,
    file_id: str,
    disposition: str = Query(
        default="inline",
        pattern="^(inline|attachment)$",
        description="inline=浏览器预览，attachment=下载原文",
    ),
) -> FileResponse:
    service = _kb_service(request)
    try:
        metadata = require_kb(service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        _raise_kb_not_found(exc)

    resolved = service.resolve_source_file(metadata=metadata, file_id=file_id)
    kind = "attachment" if disposition == "attachment" else "inline"

    return FileResponse(
        resolved.path,
        media_type=resolved.mime_type,
        headers={
            "Content-Disposition": content_disposition_header(
                disposition=kind,
                file_name=resolved.file_name,
            ),
        },
    )


@knowledge_base_router.get("/{kb_id}/files/{file_id}")
async def get_knowledge_base_file(
    request: Request,
    kb_id: str,
    file_id: str,
) -> dict[str, Any]:
    service = _kb_service(request)
    try:
        metadata = require_kb(service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        _raise_kb_not_found(exc)

    detail = service.get_file_detail(metadata=metadata, file_id=file_id)
    return success_response(
        data=detail.to_api_dict(),
        request_id=get_request_id(request),
    )


@knowledge_base_router.delete("/{kb_id}/files/{file_id}")
async def delete_knowledge_base_file(
    request: Request,
    kb_id: str,
    file_id: str,
) -> dict[str, Any]:
    service = _kb_service(request)
    try:
        metadata = require_kb(service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        _raise_kb_not_found(exc)

    service.delete_file(metadata=metadata, file_id=file_id)
    return success_response(
        data=DeleteFileResponse().model_dump(),
        request_id=get_request_id(request),
    )


@knowledge_base_router.post("/{kb_id}/files:batch-delete")
async def batch_delete_knowledge_base_files(
    request: Request,
    kb_id: str,
    body: BatchDeleteFilesRequest,
) -> dict[str, Any]:
    service = _kb_service(request)
    try:
        metadata = require_kb(service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        _raise_kb_not_found(exc)

    result = service.batch_delete_files(metadata=metadata, file_ids=body.file_ids)
    return success_response(
        data=result.to_api_dict(),
        request_id=get_request_id(request),
    )


@knowledge_base_router.get("/{kb_id}/index-stats")
async def get_knowledge_base_index_stats(request: Request, kb_id: str) -> dict[str, Any]:
    service = _kb_service(request)
    try:
        metadata = require_kb(service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        _raise_kb_not_found(exc)

    stats = service.get_index_stats(metadata=metadata)
    return success_response(
        data=stats.to_api_dict(),
        request_id=get_request_id(request),
    )
