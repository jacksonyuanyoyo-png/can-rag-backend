from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request


def get_request_id(request: Request) -> str:
    header_value = request.headers.get("X-Request-Id")
    if header_value and header_value.strip():
        return header_value.strip()
    state_request_id = getattr(request.state, "request_id", None)
    if isinstance(state_request_id, str) and state_request_id:
        return state_request_id
    return f"req_{uuid.uuid4().hex[:12]}"


def success_response(*, data: Any, request_id: str) -> dict[str, Any]:
    return {
        "data": data,
        "requestId": request_id,
    }


def paginated_response(
    *,
    data: list[Any],
    page: int,
    page_size: int,
    total: int,
    request_id: str,
) -> dict[str, Any]:
    return {
        "data": data,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "hasMore": page * page_size < total,
        },
        "requestId": request_id,
    }


def error_response(*, code: str, message: str, request_id: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
        },
        "requestId": request_id,
    }
    if details:
        payload["error"]["details"] = details
    return payload

