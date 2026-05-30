from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app.api.idempotency import (
    IDEMPOTENCY_KEY_HEADER,
    begin_idempotency,
    compute_request_hash,
    get_idempotency_key,
)
from app.core.errors import BusinessError, ErrorCode
from app.domain.idempotency import (
    IdempotencyAcquireOutcome,
    IdempotencyAcquireResult,
    IdempotencyRecord,
)
from app.repositories.idempotency_repository import IdempotencyRepository


@pytest.fixture
def idempotency_repo(database_url: str, db_connection) -> IdempotencyRepository:
    repo = IdempotencyRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return repo


def _make_request(
    *,
    method: str = "POST",
    path: str = "/v1/conversations",
    headers: dict[str, str] | None = None,
) -> Request:
    app = FastAPI()
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "path": path,
        "headers": [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in (headers or {}).items()
        ],
        "query_string": b"",
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "scheme": "http",
        "root_path": "",
    }
    return Request(scope)


def test_get_idempotency_key_missing() -> None:
    request = _make_request()
    assert get_idempotency_key(request) is None


def test_get_idempotency_key_present() -> None:
    request = _make_request(headers={IDEMPOTENCY_KEY_HEADER: "  key-abc  "})
    assert get_idempotency_key(request) == "key-abc"


def test_get_idempotency_key_empty_string() -> None:
    request = _make_request(headers={IDEMPOTENCY_KEY_HEADER: "   "})
    assert get_idempotency_key(request) is None


def test_compute_request_hash_stable() -> None:
    body = {"title": "hello", "modelId": "gpt-5"}
    first = compute_request_hash(method="post", path="/v1/conversations", body=body)
    second = compute_request_hash(method="POST", path="/v1/conversations", body=body)
    assert first == second


def test_compute_request_hash_differs_on_body() -> None:
    first = compute_request_hash(method="POST", path="/v1/conversations", body={"a": 1})
    second = compute_request_hash(method="POST", path="/v1/conversations", body={"a": 2})
    assert first != second


def test_begin_idempotency_skips_without_header() -> None:
    request = _make_request()
    repo = MagicMock(spec=IdempotencyRepository)

    result = begin_idempotency(request=request, user_id="user_1", repository=repo, body={"x": 1})

    assert result.skipped is True
    assert result.replay_response is None
    assert result.context is None
    repo.acquire.assert_not_called()


def test_begin_idempotency_replay() -> None:
    request = _make_request(headers={IDEMPOTENCY_KEY_HEADER: "key-replay"})
    repo = MagicMock(spec=IdempotencyRepository)
    record = IdempotencyRecord(
        id="idem_1",
        user_id="user_1",
        idempotency_key="key-replay",
        request_hash="hash",
        expires_at=datetime.now(UTC),
        response_status=201,
        response_body={"data": {"id": "conv_1"}, "requestId": "req_old"},
    )
    repo.acquire.return_value = IdempotencyAcquireOutcome(
        result=IdempotencyAcquireResult.REPLAY,
        record=record,
    )

    result = begin_idempotency(request=request, user_id="user_1", repository=repo, body={"title": "t"})

    assert result.replay_response is not None
    assert result.replay_response.status_code == 201
    assert json.loads(result.replay_response.body) == {
        "data": {"id": "conv_1"},
        "requestId": "req_old",
    }


def test_begin_idempotency_conflict() -> None:
    request = _make_request(headers={IDEMPOTENCY_KEY_HEADER: "key-conflict"})
    repo = MagicMock(spec=IdempotencyRepository)
    repo.acquire.return_value = IdempotencyAcquireOutcome(
        result=IdempotencyAcquireResult.CONFLICT,
        record=IdempotencyRecord(
            id="idem_1",
            user_id="user_1",
            idempotency_key="key-conflict",
            request_hash="other",
            expires_at=datetime.now(UTC),
        ),
    )

    with pytest.raises(BusinessError) as exc_info:
        begin_idempotency(request=request, user_id="user_1", repository=repo, body={"a": 1})

    assert exc_info.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert exc_info.value.details["reason"] == "request_hash_mismatch"


def test_begin_idempotency_in_progress() -> None:
    request = _make_request(headers={IDEMPOTENCY_KEY_HEADER: "key-progress"})
    repo = MagicMock(spec=IdempotencyRepository)
    repo.acquire.return_value = IdempotencyAcquireOutcome(
        result=IdempotencyAcquireResult.IN_PROGRESS,
    )

    with pytest.raises(BusinessError) as exc_info:
        begin_idempotency(request=request, user_id="user_1", repository=repo, body={"a": 1})

    assert exc_info.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert exc_info.value.details["reason"] == "in_progress"


def test_begin_idempotency_acquired_context() -> None:
    request = _make_request(headers={IDEMPOTENCY_KEY_HEADER: "key-new"})
    repo = MagicMock(spec=IdempotencyRepository)
    repo.acquire.return_value = IdempotencyAcquireOutcome(
        result=IdempotencyAcquireResult.ACQUIRED,
        record=IdempotencyRecord(
            id="idem_new",
            user_id="user_1",
            idempotency_key="key-new",
            request_hash="hash-new",
            expires_at=datetime.now(UTC),
        ),
    )

    result = begin_idempotency(request=request, user_id="user_1", repository=repo, body={"title": "x"})

    assert result.context is not None
    assert result.context.record_id == "idem_new"
    assert result.context.idempotency_key == "key-new"
    repo.acquire.assert_called_once()


def test_begin_idempotency_integration_acquire_and_in_progress(
    idempotency_repo: IdempotencyRepository,
) -> None:
    user_id = f"user_{uuid4().hex[:8]}"
    key = f"key_{uuid4().hex[:8]}"
    request = _make_request(
        headers={IDEMPOTENCY_KEY_HEADER: key},
        path="/v1/knowledge-bases",
    )
    body = {"name": "KB-A", "scope": "personal"}

    first = begin_idempotency(
        request=request,
        user_id=user_id,
        repository=idempotency_repo,
        body=body,
    )
    assert first.context is not None
    assert first.replay_response is None

    with pytest.raises(BusinessError) as exc_info:
        begin_idempotency(
            request=request,
            user_id=user_id,
            repository=idempotency_repo,
            body=body,
        )
    assert exc_info.value.details["reason"] == "in_progress"
