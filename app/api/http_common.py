from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.responses import Response


class ApiError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        status_code: int = 400,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(message)


def get_request_id(request: Request) -> str:
    header_value = request.headers.get("X-Request-Id")
    if header_value and header_value.strip():
        return header_value.strip()
    state_request_id = getattr(request.state, "request_id", None)
    if isinstance(state_request_id, str) and state_request_id:
        return state_request_id
    return f"req_{uuid4().hex[:12]}"


def success_response(*, data: Any, request_id: str) -> dict[str, Any]:
    return {"data": data, "requestId": request_id}


def error_response(*, code: str, message: str, details: dict[str, Any], request_id: str) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "details": details},
        "requestId": request_id,
    }


async def api_error_handler(request: Request, exc: ApiError) -> Response:
    request_id = get_request_id(request)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            request_id=request_id,
        ),
    )


def format_sse_event(*, event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# 2KB SSE 注释：促使 nginx/部分代理立即刷出首包，避免整段缓冲
SSE_STREAM_PREAMBLE = f": {' ' * 2048}\n\n"

SSE_STREAM_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
