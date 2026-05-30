from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.common import get_request_id, success_response
from app.api.schemas.knowledge_base import HitTestRequest, HitTestResponse
from app.core.errors import BusinessError, ErrorCode
from app.services.knowledge_base_adapter import KnowledgeBaseNotFoundError, require_kb
from app.services.knowledge_base_service import KnowledgeBaseService

hit_test_router = APIRouter(prefix="/v1/knowledge-bases", tags=["Knowledge Bases"])


def _get_service(request: Request) -> KnowledgeBaseService:
    return request.app.state.knowledge_base_service


@hit_test_router.post("/{kb_id}/hit-test")
async def hit_test_knowledge_base(
    request: Request,
    kb_id: str,
    body: HitTestRequest,
) -> dict:
    service = _get_service(request)
    try:
        metadata = require_kb(service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        raise BusinessError(ErrorCode.KB_NOT_FOUND, str(exc)) from exc

    file_ids = body.filters.file_ids if body.filters is not None else None
    data = service.hit_test(
        knowledge_base=metadata.name,
        kb_id=metadata.id,
        query=body.query,
        top_k=body.top_k,
        file_ids=file_ids,
    )
    return success_response(
        data=HitTestResponse.model_validate(data).model_dump(by_alias=True),
        request_id=get_request_id(request),
    )
