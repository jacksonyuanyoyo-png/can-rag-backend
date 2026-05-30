from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class IdempotencyAcquireResult(StrEnum):
    ACQUIRED = "acquired"
    REPLAY = "replay"
    IN_PROGRESS = "in_progress"
    CONFLICT = "conflict"


@dataclass(slots=True)
class IdempotencyRecord:
    id: str
    user_id: str
    idempotency_key: str
    request_hash: str
    expires_at: datetime
    response_status: int | None = None
    response_body: dict[str, Any] | None = None
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> IdempotencyRecord:
        response_body = row.get("response_body")
        return cls(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            idempotency_key=str(row["idempotency_key"]),
            request_hash=str(row["request_hash"]),
            expires_at=row["expires_at"],
            response_status=row.get("response_status"),
            response_body=dict(response_body) if response_body is not None else None,
            created_at=row.get("created_at"),
        )


@dataclass(slots=True)
class IdempotencyAcquireOutcome:
    result: IdempotencyAcquireResult
    record: IdempotencyRecord | None = None
