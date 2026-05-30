from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.core.errors import BusinessError, ErrorCode
from app.domain.idempotency import IdempotencyAcquireResult
from app.domain.import_job import (
    ChunkingConfig,
    ImportJob,
    ImportJobStage,
    ImportJobStatus,
    ImportJobTransitionError,
    RETRYABLE_STATUSES,
)
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.import_job_repository import ImportJobRepository

MAX_ACTIVE_IMPORTS_PER_KB = 2
HTTP_STATUS_CREATED = 201
HTTP_STATUS_OK = 200

FRONTEND_CHUNK_STRATEGY_MAP: dict[str, str] = {
    "default": "semantic",
    "custom": "fixed_size",
    "whole": "document",
    "page": "page",
}

VALID_CHUNK_STRATEGIES: frozenset[str] = frozenset(
    {
        *FRONTEND_CHUNK_STRATEGY_MAP.keys(),
        *FRONTEND_CHUNK_STRATEGY_MAP.values(),
    }
)


@dataclass(slots=True)
class ImportJobCreateRequest:
    kb_id: str
    file_ids: list[str]
    chunk_strategy: str
    meta_filename: bool = True
    meta_headings: bool = False
    chunking: ChunkingConfig | None = None


@dataclass(slots=True)
class ImportJobRetryRequest:
    job_id: str
    chunk_strategy: str | None = None
    meta_filename: bool | None = None
    meta_headings: bool | None = None
    chunking: ChunkingConfig | None = None


@dataclass(slots=True)
class ImportJobServiceResult:
    job: ImportJob
    replayed: bool = False


class ImportJobService:
    """导入任务用例服务：封装 create/get/cancel/retry 与幂等控制。"""

    def __init__(
        self,
        *,
        import_job_repository: ImportJobRepository,
        idempotency_repository: IdempotencyRepository,
        max_active_imports_per_kb: int = MAX_ACTIVE_IMPORTS_PER_KB,
    ) -> None:
        self._jobs = import_job_repository
        self._idempotency = idempotency_repository
        self._max_active_imports = max_active_imports_per_kb

    def create(
        self,
        request: ImportJobCreateRequest,
        *,
        user_id: str,
        idempotency_key: str | None = None,
    ) -> ImportJobServiceResult:
        normalized = self._normalize_create_request(request)
        hash_payload: dict[str, Any] = {
            "operation": "create_import_job",
            "kbId": normalized.kb_id,
            "fileIds": normalized.file_ids,
            "chunkStrategy": normalized.chunk_strategy,
            "metaFilename": normalized.meta_filename,
            "metaHeadings": normalized.meta_headings,
        }
        if normalized.chunking is not None:
            hash_payload["chunking"] = normalized.chunking.to_dict()
        request_hash = compute_request_hash(hash_payload)

        if idempotency_key is not None:
            replay = self._acquire_idempotency_or_raise(
                user_id=user_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return ImportJobServiceResult(job=replay, replayed=True)

        self._ensure_import_concurrency(normalized.kb_id)
        self._jobs.ensure_knowledge_base_stub(normalized.kb_id)

        try:
            job = self._create_import_job(
                kb_id=normalized.kb_id,
                file_ids=normalized.file_ids,
                chunk_strategy=normalized.chunk_strategy,
                meta_filename=normalized.meta_filename,
                meta_headings=normalized.meta_headings,
                chunking=normalized.chunking,
            )
        except ValueError as exc:
            raise BusinessError(
                ErrorCode.IMPORT_INVALID_OPTIONS,
                details={"reason": str(exc)},
            ) from exc

        if idempotency_key is not None:
            self._complete_idempotency(
                user_id=user_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_status=HTTP_STATUS_CREATED,
                response_body={"data": job.to_api_dict()},
            )

        return ImportJobServiceResult(job=job)

    def get(self, job_id: str) -> ImportJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise BusinessError(
                ErrorCode.IMPORT_JOB_NOT_FOUND,
                details={"jobId": job_id},
            )
        return job

    def cancel(self, job_id: str) -> ImportJob:
        try:
            job = self._jobs.cancel(job_id)
        except ImportJobTransitionError as exc:
            raise BusinessError(
                ErrorCode.KB_STATUS_CONFLICT,
                details={
                    "jobId": job_id,
                    "status": exc.current_status.value,
                    "reason": str(exc),
                },
            ) from exc

        if job is None:
            raise BusinessError(
                ErrorCode.IMPORT_JOB_NOT_FOUND,
                details={"jobId": job_id},
            )
        return job

    def retry(
        self,
        request: ImportJobRetryRequest,
        *,
        user_id: str,
        idempotency_key: str | None = None,
    ) -> ImportJobServiceResult:
        source = self.get(request.job_id)
        if source.status not in RETRYABLE_STATUSES:
            raise BusinessError(
                ErrorCode.KB_STATUS_CONFLICT,
                details={
                    "jobId": request.job_id,
                    "status": source.status.value,
                    "reason": "仅 failed/cancelled 状态的导入任务可重试",
                },
            )

        if request.chunking is not None:
            reject_whole_chunking_strategy(request.chunking.strategy)
            chunk_strategy = request.chunking.strategy
            meta_filename = request.chunking.meta_filename
            meta_headings = request.chunking.meta_headings
            chunking = request.chunking
        else:
            chunk_strategy = (
                normalize_chunk_strategy(request.chunk_strategy)
                if request.chunk_strategy is not None
                else (source.option.chunk_strategy if source.option else "semantic")
            )
            meta_filename = (
                request.meta_filename
                if request.meta_filename is not None
                else (source.option.meta_filename if source.option else True)
            )
            meta_headings = (
                request.meta_headings
                if request.meta_headings is not None
                else (source.option.meta_headings if source.option else False)
            )
            chunking = None

        hash_payload: dict[str, Any] = {
            "operation": "retry_import_job",
            "jobId": request.job_id,
            "chunkStrategy": chunk_strategy,
            "metaFilename": meta_filename,
            "metaHeadings": meta_headings,
        }
        if chunking is not None:
            hash_payload["chunking"] = chunking.to_dict()
        request_hash = compute_request_hash(hash_payload)

        if idempotency_key is not None:
            replay = self._acquire_idempotency_or_raise(
                user_id=user_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return ImportJobServiceResult(job=replay, replayed=True)

        self._ensure_import_concurrency(source.kb_id)

        try:
            job = self._create_import_job(
                kb_id=source.kb_id,
                file_ids=list(source.file_ids),
                chunk_strategy=chunk_strategy,
                meta_filename=meta_filename,
                meta_headings=meta_headings,
                chunking=chunking,
                retry_of=source.id,
            )
        except ValueError as exc:
            raise BusinessError(
                ErrorCode.IMPORT_INVALID_OPTIONS,
                details={"reason": str(exc)},
            ) from exc

        if idempotency_key is not None:
            self._complete_idempotency(
                user_id=user_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_status=HTTP_STATUS_CREATED,
                response_body={"data": job.to_api_dict()},
            )

        return ImportJobServiceResult(job=job)

    def _create_import_job(
        self,
        *,
        kb_id: str,
        file_ids: list[str],
        chunk_strategy: str,
        meta_filename: bool,
        meta_headings: bool,
        chunking: ChunkingConfig | None = None,
        retry_of: str | None = None,
    ) -> ImportJob:
        base_kwargs: dict[str, Any] = {
            "kb_id": kb_id,
            "file_ids": file_ids,
            "chunk_strategy": chunk_strategy,
            "meta_filename": meta_filename,
            "meta_headings": meta_headings,
        }
        if retry_of is not None:
            base_kwargs["retry_of"] = retry_of

        if chunking is not None:
            config_dict = chunking.to_dict()
            try:
                return self._jobs.create(**base_kwargs, chunking_config=config_dict)
            except TypeError:
                return self._jobs.create(**base_kwargs)

        return self._jobs.create(**base_kwargs)

    def _normalize_create_request(self, request: ImportJobCreateRequest) -> ImportJobCreateRequest:
        if not request.file_ids:
            raise BusinessError(
                ErrorCode.IMPORT_INVALID_OPTIONS,
                details={"field": "fileIds", "reason": "至少需要一个 fileId"},
            )

        deduped_file_ids = list(dict.fromkeys(request.file_ids))

        if request.chunking is not None:
            reject_whole_chunking_strategy(request.chunking.strategy)
            return ImportJobCreateRequest(
                kb_id=request.kb_id,
                file_ids=deduped_file_ids,
                chunk_strategy=request.chunking.strategy,
                meta_filename=request.chunking.meta_filename,
                meta_headings=request.chunking.meta_headings,
                chunking=request.chunking,
            )

        return ImportJobCreateRequest(
            kb_id=request.kb_id,
            file_ids=deduped_file_ids,
            chunk_strategy=normalize_chunk_strategy(request.chunk_strategy),
            meta_filename=request.meta_filename,
            meta_headings=request.meta_headings,
            chunking=None,
        )

    def _ensure_import_concurrency(self, kb_id: str) -> None:
        active_count = self._jobs.count_active_by_kb(kb_id)
        if active_count >= self._max_active_imports:
            raise BusinessError(
                ErrorCode.IMPORT_CONCURRENCY_LIMIT,
                details={
                    "kbId": kb_id,
                    "activeCount": active_count,
                    "limit": self._max_active_imports,
                },
            )

    def _acquire_idempotency_or_raise(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> ImportJob | None:
        outcome = self._idempotency.acquire(
            user_id=user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )

        if outcome.result == IdempotencyAcquireResult.CONFLICT:
            raise BusinessError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                details={"idempotencyKey": idempotency_key},
            )

        if outcome.result == IdempotencyAcquireResult.IN_PROGRESS:
            raise BusinessError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                message="Idempotency key is already in progress",
                details={"idempotencyKey": idempotency_key},
            )

        if outcome.result == IdempotencyAcquireResult.REPLAY:
            return self._job_from_idempotency_response(outcome.record.response_body)

        return None

    def _complete_idempotency(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        request_hash: str,
        response_status: int,
        response_body: dict[str, Any],
    ) -> None:
        record = self._idempotency.get(user_id, idempotency_key)
        if record is None or record.request_hash != request_hash:
            return
        self._idempotency.complete(
            record.id,
            response_status=response_status,
            response_body=response_body,
        )

    @staticmethod
    def _job_from_idempotency_response(response_body: dict[str, Any] | None) -> ImportJob:
        if not response_body:
            raise BusinessError(ErrorCode.INTERNAL_ERROR, message="Idempotency replay body missing")

        payload = response_body.get("data")
        if not isinstance(payload, dict):
            raise BusinessError(ErrorCode.INTERNAL_ERROR, message="Idempotency replay payload invalid")

        return ImportJob(
            id=str(payload["id"]),
            kb_id=str(payload.get("knowledgeBaseId", payload.get("kb_id", ""))),
            file_ids=[str(file_id) for file_id in payload.get("fileIds", [])],
            status=ImportJobStatus(str(payload["status"])),
            progress=int(payload.get("progress", 0)),
            stage=ImportJobStage(str(payload["stage"])),
            error_code=payload.get("errorCode"),
            error_message=payload.get("errorMessage"),
            retry_of=payload.get("retryOf"),
        )


def reject_whole_chunking_strategy(strategy: str) -> None:
    if strategy == "whole":
        raise BusinessError(
            ErrorCode.IMPORT_INVALID_OPTIONS,
            details={"field": "chunking.strategy", "reason": "strategy=whole 暂未开放"},
        )


def normalize_chunk_strategy(value: str) -> str:
    reject_whole_chunking_strategy(value)
    normalized = FRONTEND_CHUNK_STRATEGY_MAP.get(value, value)
    if normalized not in VALID_CHUNK_STRATEGIES:
        raise BusinessError(
            ErrorCode.IMPORT_INVALID_OPTIONS,
            details={"field": "chunkStrategy", "value": value},
        )
    return normalized


def compute_request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
