from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status

from app.api.common import get_request_id, success_response
from app.api.idempotency import get_idempotency_key
from app.api.schemas.web_import import CreateWebImportRequest
from app.core.dependencies import extract_bearer_token, get_auth_service, require_app_state_service
from app.core.errors import BusinessError, ErrorCode
from app.domain.import_job import ChunkingConfig
from app.schemas.auth import UserMePublic
from app.services.auth.auth_service import AuthService
from app.services.knowledge_base_adapter import KnowledgeBaseNotFoundError, require_kb
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.web_import_service import WebImportService

logger = logging.getLogger(__name__)

web_import_router = APIRouter(prefix="/v1/knowledge-bases", tags=["Web Import"])


def get_web_import_service(request: Request) -> WebImportService:
    return require_app_state_service(request, "web_import_service", "WebImportService")


def _kb_service(request: Request) -> KnowledgeBaseService:
    return require_app_state_service(
        request,
        "knowledge_base_service",
        "KnowledgeBaseService",
    )


WebImportServiceDep = Annotated[WebImportService, Depends(get_web_import_service)]


def _require_web_import(user: UserMePublic) -> None:
    if "kb:file:upload" not in user.permissions:
        raise BusinessError(ErrorCode.AUTH_FORBIDDEN)


def _current_user(
    access_token: str = Depends(extract_bearer_token),
    auth_service: AuthService = Depends(get_auth_service),
) -> UserMePublic:
    return auth_service.me(access_token=access_token)


CurrentUserDep = Annotated[UserMePublic, Depends(_current_user)]


@web_import_router.post("/{kb_id}/web-imports", status_code=status.HTTP_201_CREATED)
async def create_web_import(
    request: Request,
    kb_id: str,
    body: CreateWebImportRequest,
    service: WebImportServiceDep,
    user: CurrentUserDep,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    _require_web_import(user)
    kb_service = _kb_service(request)
    try:
        require_kb(kb_service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        raise BusinessError(ErrorCode.KB_NOT_FOUND, str(exc)) from exc

    chunking: ChunkingConfig | None = None
    meta_filename = True
    meta_headings = True
    chunk_strategy = body.chunk_strategy
    if body.chunking is not None:
        chunking = ChunkingConfig.from_chunking_options(
            body.chunking,
            fallback_strategy=body.chunk_strategy,
            metadata=body.chunking.metadata,
            parsing=body.parsing,
        )
        chunk_strategy = chunking.strategy
        if body.chunking.metadata is not None:
            meta_filename = body.chunking.metadata.include_file_name
            meta_headings = body.chunking.metadata.include_headings
    elif body.parsing is not None:
        chunking = ChunkingConfig.default(
            chunk_strategy,
            meta_filename=meta_filename,
            meta_headings=meta_headings,
            parsing=body.parsing,
        )

    use_browser = body.use_browser_fallback
    if use_browser is None and body.parsing is not None:
        use_browser = body.parsing.web_use_browser_fallback

    result = service.import_url(
        knowledge_base_id=kb_id,
        url=str(body.url),
        user_id=user.id,
        use_browser_fallback=use_browser,
        auto_import=body.auto_import,
        chunking=chunking,
        chunk_strategy=chunk_strategy,
        meta_filename=meta_filename,
        meta_headings=meta_headings,
        idempotency_key=get_idempotency_key(request),
    )

    if body.auto_import and result.import_job_id is not None:
        worker = getattr(request.app.state, "import_job_worker", None)
        if worker is not None:
            background_tasks.add_task(worker.run_job, result.import_job_id)
        else:
            logger.warning(
                "import_job_worker 未装配，跳过后台执行 job_id=%s",
                result.import_job_id,
            )

    return success_response(
        data=result.to_api_dict(),
        request_id=get_request_id(request),
    )
