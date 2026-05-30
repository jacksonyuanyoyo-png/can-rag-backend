from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.idempotency import IdempotencyAcquireResult
from app.repositories.idempotency_repository import IdempotencyRepository


@pytest.fixture
def idempotency_repo(database_url: str, db_connection) -> IdempotencyRepository:
    repo = IdempotencyRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return repo


def test_acquire_insert_and_replay(idempotency_repo: IdempotencyRepository) -> None:
    user_id = f"user_{uuid4().hex[:8]}"
    key = f"key_{uuid4().hex[:8]}"
    request_hash = "hash-create-import-job"

    first = idempotency_repo.acquire(
        user_id=user_id,
        idempotency_key=key,
        request_hash=request_hash,
    )
    assert first.result == IdempotencyAcquireResult.ACQUIRED
    assert first.record is not None

    completed = idempotency_repo.complete(
        first.record.id,
        response_status=201,
        response_body={"data": {"id": "job_1"}},
    )
    assert completed is not None
    assert completed.response_status == 201
    assert completed.response_body == {"data": {"id": "job_1"}}

    replay = idempotency_repo.acquire(
        user_id=user_id,
        idempotency_key=key,
        request_hash=request_hash,
    )
    assert replay.result == IdempotencyAcquireResult.REPLAY
    assert replay.record is not None
    assert replay.record.response_body == {"data": {"id": "job_1"}}


def test_acquire_conflict_on_different_request_hash(
    idempotency_repo: IdempotencyRepository,
) -> None:
    user_id = f"user_{uuid4().hex[:8]}"
    key = f"key_{uuid4().hex[:8]}"

    acquired = idempotency_repo.acquire(
        user_id=user_id,
        idempotency_key=key,
        request_hash="hash-a",
    )
    assert acquired.result == IdempotencyAcquireResult.ACQUIRED

    conflict = idempotency_repo.acquire(
        user_id=user_id,
        idempotency_key=key,
        request_hash="hash-b",
    )
    assert conflict.result == IdempotencyAcquireResult.CONFLICT
    assert conflict.record is not None
    assert conflict.record.request_hash == "hash-a"


def test_acquire_in_progress_before_complete(
    idempotency_repo: IdempotencyRepository,
) -> None:
    user_id = f"user_{uuid4().hex[:8]}"
    key = f"key_{uuid4().hex[:8]}"
    request_hash = "hash-in-progress"

    acquired = idempotency_repo.acquire(
        user_id=user_id,
        idempotency_key=key,
        request_hash=request_hash,
    )
    assert acquired.result == IdempotencyAcquireResult.ACQUIRED

    in_progress = idempotency_repo.acquire(
        user_id=user_id,
        idempotency_key=key,
        request_hash=request_hash,
    )
    assert in_progress.result == IdempotencyAcquireResult.IN_PROGRESS


def test_delete_expired(idempotency_repo: IdempotencyRepository) -> None:
    user_id = f"user_{uuid4().hex[:8]}"
    key = f"key_{uuid4().hex[:8]}"
    record_id = f"idem_{uuid4().hex}"

    acquired = idempotency_repo.acquire(
        user_id=user_id,
        idempotency_key=key,
        request_hash="hash-expired",
        record_id=record_id,
        ttl_seconds=60,
    )
    assert acquired.record is not None

    deleted = idempotency_repo.delete_expired(
        before=datetime.now(UTC) + timedelta(minutes=2),
    )
    assert deleted >= 1
    assert idempotency_repo.get(user_id, key) is None
