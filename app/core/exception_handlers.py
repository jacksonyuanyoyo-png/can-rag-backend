from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import BusinessError, ErrorCode, default_message_for_code, http_status_for_code
from app.core.responses import error_response, get_request_id

logger = logging.getLogger(__name__)


def _validation_details(errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {"fields": errors}


async def business_error_handler(request: Request, exc: BusinessError) -> Any:
    return error_response(
        exc.code,
        exc.message,
        request,
        details=exc.details,
        status_code=exc.http_status,
    )


async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> Any:
    return error_response(
        ErrorCode.VALIDATION_ERROR,
        default_message_for_code(ErrorCode.VALIDATION_ERROR),
        request,
        details=_validation_details(exc.errors()),
        status_code=http_status_for_code(ErrorCode.VALIDATION_ERROR),
    )


async def http_exception_handler(request: Request, exc: HTTPException | StarletteHTTPException) -> Any:
    status_code = exc.status_code
    detail = exc.detail

    if isinstance(detail, dict):
        code = str(detail.get("code", ErrorCode.RESOURCE_NOT_FOUND))
        message = str(detail.get("message", default_message_for_code(code)))
        details = detail.get("details")
        if not isinstance(details, dict):
            details = {}
        return error_response(code, message, request, details=details, status_code=status_code)

    if status_code == 401:
        code = ErrorCode.AUTH_TOKEN_INVALID
    elif status_code == 403:
        code = ErrorCode.AUTH_FORBIDDEN
    elif status_code == 404:
        code = ErrorCode.RESOURCE_NOT_FOUND
    elif status_code == 409:
        code = ErrorCode.IDEMPOTENCY_CONFLICT
    elif status_code == 422:
        code = ErrorCode.VALIDATION_ERROR
    elif status_code == 429:
        code = ErrorCode.RATE_LIMITED
    else:
        code = ErrorCode.INTERNAL_ERROR

    message = str(detail) if detail else default_message_for_code(code)
    return error_response(code, message, request, status_code=status_code)


async def unhandled_exception_handler(request: Request, exc: Exception) -> Any:
    request_id = get_request_id(request)
    logger.exception("Unhandled exception requestId=%s", request_id, exc_info=exc)
    return error_response(
        ErrorCode.INTERNAL_ERROR,
        default_message_for_code(ErrorCode.INTERNAL_ERROR),
        request,
        status_code=http_status_for_code(ErrorCode.INTERNAL_ERROR),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(BusinessError, business_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
