from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status

from app.api.common import get_request_id, success_response
from app.api.idempotency import get_idempotency_key
from app.api.schemas.import_job import (
    CreateImportJobRequest,
    ImportJobMetadataOptions,
    RetryImportJobRequest,
)
from app.core.dependencies import extract_bearer_token, get_auth_service, require_app_state_service
from app.core.errors import BusinessError, ErrorCode
from app.domain.import_job import ChunkingConfig, ImportJob
from app.schemas.auth import UserMePublic
from app.services.auth.auth_service import AuthService
from app.services.import_job_service import (
    ImportJobCreateRequest,
    ImportJobRetryRequest,
    ImportJobService,
)
from app.services.knowledge_base_adapter import KnowledgeBaseNotFoundError, require_kb
from app.services.knowledge_base_service import KnowledgeBaseService

logger = logging.getLogger(__name__)

import_job_router = APIRouter(prefix="/v1/import-jobs", tags=["Import Jobs"])
kb_import_job_router = APIRouter(prefix="/v1/knowledge-bases", tags=["Import Jobs"])


def get_import_job_service(request: Request) -> ImportJobService:
    return require_app_state_service(request, "import_job_service", "ImportJobService")


def _kb_service(request: Request) -> KnowledgeBaseService:
    return require_app_state_service(
        request,
        "knowledge_base_service",
        "KnowledgeBaseService",
    )


ImportJobServiceDep = Annotated[ImportJobService, Depends(get_import_job_service)]


def _require_kb_import(user: UserMePublic) -> None:
    if "kb:import" not in user.permissions:
        raise BusinessError(ErrorCode.AUTH_FORBIDDEN)


def _current_user(
    access_token: str = Depends(extract_bearer_token),
    auth_service: AuthService = Depends(get_auth_service),
) -> UserMePublic:
    return auth_service.me(access_token=access_token)


CurrentUserDep = Annotated[UserMePublic, Depends(_current_user)]


def _import_job_cancelled(job: ImportJob) -> dict[str, Any]:
    return {"id": job.id, "status": job.status.value, "progress": job.progress}


def _import_job_retried(job: ImportJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "retryOf": job.retry_of,
        "status": job.status.value,
        "progress": job.progress,
    }


@kb_import_job_router.post("/{kb_id}/import-jobs", status_code=status.HTTP_201_CREATED)
async def create_import_job(
    request: Request,
    kb_id: str,
    body: CreateImportJobRequest,
    service: ImportJobServiceDep,
    user: CurrentUserDep,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    _require_kb_import(user)
    kb_service = _kb_service(request)
    try:
        require_kb(kb_service, kb_id)
    except KnowledgeBaseNotFoundError as exc:
        raise BusinessError(ErrorCode.KB_NOT_FOUND, str(exc)) from exc

    chunking = ChunkingConfig.from_chunking_options(
        body.chunking,
        fallback_strategy=body.chunk_strategy,
        metadata=body.metadata,
        parsing=body.parsing,
    )
    result = service.create(
        ImportJobCreateRequest(
            kb_id=kb_id,
            file_ids=body.file_ids,
            chunk_strategy=chunking.strategy,
            meta_filename=chunking.meta_filename,
            meta_headings=chunking.meta_headings,
            chunking=chunking,
        ),
        user_id=user.id,
        idempotency_key=get_idempotency_key(request),
    )
    if not result.replayed:
        worker = getattr(request.app.state, "import_job_worker", None)
        if worker is not None:
            background_tasks.add_task(worker.run_job, result.job.id)
        else:
            logger.warning(
                "import_job_worker 未装配，跳过后台执行 job_id=%s",
                result.job.id,
            )
    return success_response(
        data=result.job.to_api_dict(),
        request_id=get_request_id(request),
    )


@import_job_router.get("/{job_id}")
async def get_import_job(
    request: Request,
    job_id: str,
    service: ImportJobServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_kb_import(user)
    job = service.get(job_id)
    return success_response(
        data=job.to_api_dict(),
        request_id=get_request_id(request),
    )


@import_job_router.post("/{job_id}:cancel")
async def cancel_import_job(
    request: Request,
    job_id: str,
    service: ImportJobServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_kb_import(user)
    job = service.cancel(job_id)
    return success_response(
        data=_import_job_cancelled(job),
        request_id=get_request_id(request),
    )


@import_job_router.post("/{job_id}:retry", status_code=status.HTTP_201_CREATED)
async def retry_import_job(
    request: Request,
    job_id: str,
    body: RetryImportJobRequest,
    service: ImportJobServiceDep,
    user: CurrentUserDep,
) -> dict[str, Any]:
    _require_kb_import(user)
    options = body.options
    fallback_strategy = (
        options.chunk_strategy
        if options is not None and options.chunk_strategy is not None
        else "default"
    )
    retry_metadata = ImportJobMetadataOptions(
        include_file_name=(
            options.include_file_name
            if options is not None and options.include_file_name is not None
            else True
        ),
        include_headings=(
            options.include_headings
            if options is not None and options.include_headings is not None
            else False
        ),
    )
    chunking = None
    if body.chunking is not None or body.parsing is not None:
        chunking = ChunkingConfig.from_chunking_options(
            body.chunking,
            fallback_strategy=fallback_strategy,
            metadata=retry_metadata,
            parsing=body.parsing,
        )
    result = service.retry(
        ImportJobRetryRequest(
            job_id=job_id,
            chunk_strategy=options.chunk_strategy if options is not None else None,
            meta_filename=options.include_file_name if options is not None else None,
            meta_headings=options.include_headings if options is not None else None,
            chunking=chunking,
        ),
        user_id=user.id,
        idempotency_key=get_idempotency_key(request),
    )
    return success_response(
        data=_import_job_retried(result.job),
        request_id=get_request_id(request),
    )
