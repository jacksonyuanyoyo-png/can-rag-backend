from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.core.errors import ErrorCode, http_status_for_code


REQUEST_ID_HEADER = "X-Request-Id"


def new_request_id() -> str:
    return f"req_{uuid4().hex}"


def get_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id
    return new_request_id()


def success_body(data: Any, request_id: str) -> dict[str, Any]:
    return {"data": data, "requestId": request_id}


def paginated_body(
    items: list[Any],
    *,
    page: int,
    page_size: int,
    total: int,
    request_id: str,
) -> dict[str, Any]:
    safe_page = max(page, 1)
    safe_page_size = max(page_size, 1)
    return {
        "data": items,
        "pagination": {
            "page": safe_page,
            "pageSize": safe_page_size,
            "total": total,
            "hasMore": safe_page * safe_page_size < total,
        },
        "requestId": request_id,
    }


def error_body(
    code: ErrorCode | str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    request_id: str,
) -> dict[str, Any]:
    code_value = code.value if isinstance(code, ErrorCode) else str(code)
    return {
        "error": {
            "code": code_value,
            "message": message,
            "details": details or {},
        },
        "requestId": request_id,
    }


def success_response(
    data: Any,
    request: Request,
    *,
    status_code: int = 200,
) -> JSONResponse:
    request_id = get_request_id(request)
    response = JSONResponse(status_code=status_code, content=success_body(data, request_id))
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


def paginated_response(
    items: list[Any],
    *,
    page: int,
    page_size: int,
    total: int,
    request: Request,
    status_code: int = 200,
) -> JSONResponse:
    request_id = get_request_id(request)
    body = paginated_body(items, page=page, page_size=page_size, total=total, request_id=request_id)
    response = JSONResponse(status_code=status_code, content=body)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


def error_response(
    code: ErrorCode | str,
    message: str,
    request: Request,
    *,
    details: dict[str, Any] | None = None,
    status_code: int | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    resolved_status = status_code if status_code is not None else http_status_for_code(code)
    body = error_body(code, message, details=details, request_id=request_id)
    response = JSONResponse(status_code=resolved_status, content=body)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


def attach_request_id(response: Response, request_id: str) -> Response:
    response.headers[REQUEST_ID_HEADER] = request_id
    return response
