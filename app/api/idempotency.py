from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.errors import BusinessError, ErrorCode
from app.core.responses import attach_request_id, get_request_id
from app.domain.idempotency import IdempotencyAcquireResult, IdempotencyRecord
from app.repositories.idempotency_repository import IdempotencyRepository

IDEMPOTENCY_KEY_HEADER = "X-Idempotency-Key"
DEFAULT_IDEMPOTENCY_TTL_SECONDS = 86400


def get_idempotency_key(request: Request) -> str | None:
    """从请求头读取幂等键；未提供或为空时返回 None。"""
    raw = request.headers.get(IDEMPOTENCY_KEY_HEADER)
    if raw is None:
        return None
    key = raw.strip()
    return key or None


def _normalize_body(body: Any) -> Any:
    if body is None:
        return None
    if hasattr(body, "model_dump"):
        return body.model_dump(mode="json")
    if isinstance(body, bytes):
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return body.hex()
    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body
    return body


def compute_request_hash(*, method: str, path: str, body: Any = None) -> str:
    """对 method + path + 规范化 body 计算 SHA-256 请求指纹。"""
    payload = {
        "method": method.upper(),
        "path": path,
        "body": _normalize_body(body),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_replay_response(*, record: IdempotencyRecord, request: Request) -> JSONResponse:
    """根据已缓存的幂等记录构造重放响应。"""
    request_id = get_request_id(request)
    status_code = record.response_status or 200
    body = record.response_body if record.response_body is not None else {}
    response = JSONResponse(status_code=status_code, content=body)
    return attach_request_id(response, request_id)


def get_idempotency_repository(request: Request) -> IdempotencyRepository:
    """FastAPI 依赖：获取 lifespan 注入的幂等仓储。"""
    from app.core.dependencies import require_app_state_service

    return require_app_state_service(
        request,
        "idempotency_repository",
        "IdempotencyRepository",
    )


@dataclass(slots=True)
class IdempotencyContext:
    """已成功 acquire 的幂等上下文，用于在业务完成后缓存响应。"""

    idempotency_key: str
    record_id: str
    request_hash: str
    _repository: IdempotencyRepository

    def cache_response(self, *, status_code: int, response_body: dict[str, Any]) -> None:
        self._repository.complete(
            self.record_id,
            response_status=status_code,
            response_body=response_body,
        )


@dataclass(slots=True)
class IdempotencyBeginResult:
    """begin_idempotency 的结果：跳过重放、直接重放或进入执行业务。"""

    skipped: bool = False
    replay_response: JSONResponse | None = None
    context: IdempotencyContext | None = None


def _raise_idempotency_conflict(*, idempotency_key: str, reason: str) -> None:
    raise BusinessError(
        ErrorCode.IDEMPOTENCY_CONFLICT,
        details={"idempotencyKey": idempotency_key, "reason": reason},
    )


def begin_idempotency(
    *,
    request: Request,
    user_id: str,
    repository: IdempotencyRepository,
    method: str | None = None,
    path: str | None = None,
    body: Any = None,
    ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
) -> IdempotencyBeginResult:
    """
    幂等前置检查：无键则跳过；有键则 acquire 并处理 replay / conflict / in_progress。

    业务路由典型用法::

        result = begin_idempotency(request=request, user_id=user_id, repository=repo, body=body)
        if result.replay_response is not None:
            return result.replay_response
        # ... 执行业务 ...
        if result.context is not None:
            result.context.cache_response(status_code=201, response_body=success_body(...))
    """
    idempotency_key = get_idempotency_key(request)
    if idempotency_key is None:
        return IdempotencyBeginResult(skipped=True)

    resolved_method = (method or request.method).upper()
    resolved_path = path or request.url.path
    request_hash = compute_request_hash(method=resolved_method, path=resolved_path, body=body)

    outcome = repository.acquire(
        user_id=user_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        ttl_seconds=ttl_seconds,
    )

    if outcome.result == IdempotencyAcquireResult.REPLAY:
        if outcome.record is None:
            raise BusinessError(ErrorCode.INTERNAL_ERROR, message="Idempotency replay record missing")
        return IdempotencyBeginResult(
            replay_response=build_replay_response(record=outcome.record, request=request),
        )

    if outcome.result == IdempotencyAcquireResult.CONFLICT:
        _raise_idempotency_conflict(idempotency_key=idempotency_key, reason="request_hash_mismatch")

    if outcome.result == IdempotencyAcquireResult.IN_PROGRESS:
        _raise_idempotency_conflict(idempotency_key=idempotency_key, reason="in_progress")

    if outcome.record is None:
        raise BusinessError(ErrorCode.INTERNAL_ERROR, message="Idempotency acquire record missing")

    return IdempotencyBeginResult(
        context=IdempotencyContext(
            idempotency_key=idempotency_key,
            record_id=outcome.record.id,
            request_hash=request_hash,
            _repository=repository,
        ),
    )


@asynccontextmanager
async def idempotency_guard(
    *,
    request: Request,
    user_id: str,
    repository: IdempotencyRepository,
    method: str | None = None,
    path: str | None = None,
    body: Any = None,
    ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
) -> AsyncIterator[tuple[IdempotencyBeginResult, IdempotencyContext | None]]:
    """
    异步上下文形式的幂等守卫。

    yield (begin_result, context)；若 begin_result.replay_response 非空，调用方应直接返回该响应。
    上下文退出时不自动 cache，需显式调用 context.cache_response。
    """
    begin_result = begin_idempotency(
        request=request,
        user_id=user_id,
        repository=repository,
        method=method,
        path=path,
        body=body,
        ttl_seconds=ttl_seconds,
    )
    yield begin_result, begin_result.context
