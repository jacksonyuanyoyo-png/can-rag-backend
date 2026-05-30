from __future__ import annotations

from enum import StrEnum
from http import HTTPStatus
from typing import Any


class ErrorCode(StrEnum):
    """业务错误码，与 API 文档 §9 对齐。"""

    # Auth
    AUTH_INVALID_REQUEST = "AUTH_INVALID_REQUEST"
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_TOKEN_MISSING = "AUTH_TOKEN_MISSING"
    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_REFRESH_EXPIRED = "AUTH_REFRESH_EXPIRED"
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"

    # Conversations & Messages
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    CONVERSATION_ARCHIVED = "CONVERSATION_ARCHIVED"
    CONVERSATION_RATE_LIMITED = "CONVERSATION_RATE_LIMITED"
    MESSAGE_EMPTY = "MESSAGE_EMPTY"
    MESSAGE_TOO_LONG = "MESSAGE_TOO_LONG"
    MESSAGE_ALREADY_RUNNING = "MESSAGE_ALREADY_RUNNING"
    MESSAGE_GENERATION_FAILED = "MESSAGE_GENERATION_FAILED"
    MESSAGE_CANCELLED = "MESSAGE_CANCELLED"

    # Folders & Templates
    FOLDER_NOT_FOUND = "FOLDER_NOT_FOUND"
    FOLDER_NAME_DUPLICATED = "FOLDER_NAME_DUPLICATED"
    FOLDER_INVALID_PARENT = "FOLDER_INVALID_PARENT"
    TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"
    TEMPLATE_NAME_DUPLICATED = "TEMPLATE_NAME_DUPLICATED"
    TEMPLATE_SCOPE_FORBIDDEN = "TEMPLATE_SCOPE_FORBIDDEN"

    # Knowledge bases & files
    KB_NOT_FOUND = "KB_NOT_FOUND"
    KB_NAME_DUPLICATED = "KB_NAME_DUPLICATED"
    KB_PERMISSION_DENIED = "KB_PERMISSION_DENIED"
    KB_STATUS_CONFLICT = "KB_STATUS_CONFLICT"
    KB_HAS_RUNNING_IMPORT = "KB_HAS_RUNNING_IMPORT"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_TYPE_UNSUPPORTED = "FILE_TYPE_UNSUPPORTED"
    FILE_SIZE_EXCEEDED = "FILE_SIZE_EXCEEDED"
    FILE_DUPLICATED = "FILE_DUPLICATED"
    FILE_IN_USE = "FILE_IN_USE"

    # Import, hit-test & common
    IMPORT_JOB_NOT_FOUND = "IMPORT_JOB_NOT_FOUND"
    IMPORT_INVALID_OPTIONS = "IMPORT_INVALID_OPTIONS"
    IMPORT_CONCURRENCY_LIMIT = "IMPORT_CONCURRENCY_LIMIT"
    IMPORT_PARSE_FAILED = "IMPORT_PARSE_FAILED"
    IMPORT_CHUNK_FAILED = "IMPORT_CHUNK_FAILED"
    IMPORT_EMBEDDING_FAILED = "IMPORT_EMBEDDING_FAILED"
    IMPORT_INDEX_FAILED = "IMPORT_INDEX_FAILED"
    HIT_TEST_EMPTY_QUERY = "HIT_TEST_EMPTY_QUERY"
    HIT_TEST_INVALID_TOPK = "HIT_TEST_INVALID_TOPK"
    HIT_TEST_INDEX_NOT_READY = "HIT_TEST_INDEX_NOT_READY"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


DEFAULT_ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.AUTH_INVALID_REQUEST: "Invalid authentication request",
    ErrorCode.AUTH_INVALID_CREDENTIALS: "Invalid credentials",
    ErrorCode.AUTH_TOKEN_MISSING: "Authentication token is missing",
    ErrorCode.AUTH_TOKEN_INVALID: "Invalid token",
    ErrorCode.AUTH_TOKEN_EXPIRED: "Token expired",
    ErrorCode.AUTH_REFRESH_EXPIRED: "Refresh token expired",
    ErrorCode.AUTH_FORBIDDEN: "Forbidden",
    ErrorCode.CONVERSATION_NOT_FOUND: "Conversation not found",
    ErrorCode.CONVERSATION_ARCHIVED: "Conversation is archived",
    ErrorCode.CONVERSATION_RATE_LIMITED: "Conversation rate limit exceeded",
    ErrorCode.MESSAGE_EMPTY: "Message cannot be empty",
    ErrorCode.MESSAGE_TOO_LONG: "Message exceeds maximum length",
    ErrorCode.MESSAGE_ALREADY_RUNNING: "A message generation is already running",
    ErrorCode.MESSAGE_GENERATION_FAILED: "Message generation failed",
    ErrorCode.MESSAGE_CANCELLED: "Message generation was cancelled",
    ErrorCode.FOLDER_NOT_FOUND: "Folder not found",
    ErrorCode.FOLDER_NAME_DUPLICATED: "Folder name already exists",
    ErrorCode.FOLDER_INVALID_PARENT: "Invalid parent folder",
    ErrorCode.TEMPLATE_NOT_FOUND: "Template not found",
    ErrorCode.TEMPLATE_NAME_DUPLICATED: "Template name already exists",
    ErrorCode.TEMPLATE_SCOPE_FORBIDDEN: "Template scope is not allowed",
    ErrorCode.KB_NOT_FOUND: "Knowledge base not found",
    ErrorCode.KB_NAME_DUPLICATED: "Knowledge base name already exists",
    ErrorCode.KB_PERMISSION_DENIED: "Knowledge base permission denied",
    ErrorCode.KB_STATUS_CONFLICT: "Knowledge base status conflict",
    ErrorCode.KB_HAS_RUNNING_IMPORT: "Knowledge base has a running import job",
    ErrorCode.FILE_NOT_FOUND: "File not found",
    ErrorCode.FILE_TYPE_UNSUPPORTED: "Unsupported file type",
    ErrorCode.FILE_SIZE_EXCEEDED: "File size exceeds limit",
    ErrorCode.FILE_DUPLICATED: "File already exists",
    ErrorCode.FILE_IN_USE: "File is in use",
    ErrorCode.IMPORT_JOB_NOT_FOUND: "Import job not found",
    ErrorCode.IMPORT_INVALID_OPTIONS: "Invalid import options",
    ErrorCode.IMPORT_CONCURRENCY_LIMIT: "Import concurrency limit reached",
    ErrorCode.IMPORT_PARSE_FAILED: "Import parse failed",
    ErrorCode.IMPORT_CHUNK_FAILED: "Import chunking failed",
    ErrorCode.IMPORT_EMBEDDING_FAILED: "Import embedding failed",
    ErrorCode.IMPORT_INDEX_FAILED: "Import indexing failed",
    ErrorCode.HIT_TEST_EMPTY_QUERY: "Hit test query cannot be empty",
    ErrorCode.HIT_TEST_INVALID_TOPK: "Invalid hit test topK",
    ErrorCode.HIT_TEST_INDEX_NOT_READY: "Knowledge base index is not ready",
    ErrorCode.VALIDATION_ERROR: "Validation failed",
    ErrorCode.RESOURCE_NOT_FOUND: "Resource not found",
    ErrorCode.IDEMPOTENCY_CONFLICT: "Idempotency key conflict",
    ErrorCode.RATE_LIMITED: "Rate limit exceeded",
    ErrorCode.INTERNAL_ERROR: "Internal server error",
}


ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.AUTH_INVALID_REQUEST: HTTPStatus.BAD_REQUEST,
    ErrorCode.AUTH_INVALID_CREDENTIALS: HTTPStatus.UNAUTHORIZED,
    ErrorCode.AUTH_TOKEN_MISSING: HTTPStatus.UNAUTHORIZED,
    ErrorCode.AUTH_TOKEN_INVALID: HTTPStatus.UNAUTHORIZED,
    ErrorCode.AUTH_TOKEN_EXPIRED: HTTPStatus.UNAUTHORIZED,
    ErrorCode.AUTH_REFRESH_EXPIRED: HTTPStatus.UNAUTHORIZED,
    ErrorCode.AUTH_FORBIDDEN: HTTPStatus.FORBIDDEN,
    ErrorCode.CONVERSATION_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.CONVERSATION_ARCHIVED: HTTPStatus.CONFLICT,
    ErrorCode.CONVERSATION_RATE_LIMITED: HTTPStatus.TOO_MANY_REQUESTS,
    ErrorCode.MESSAGE_EMPTY: HTTPStatus.BAD_REQUEST,
    ErrorCode.MESSAGE_TOO_LONG: HTTPStatus.BAD_REQUEST,
    ErrorCode.MESSAGE_ALREADY_RUNNING: HTTPStatus.CONFLICT,
    ErrorCode.MESSAGE_GENERATION_FAILED: HTTPStatus.INTERNAL_SERVER_ERROR,
    ErrorCode.MESSAGE_CANCELLED: HTTPStatus.CONFLICT,
    ErrorCode.FOLDER_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.FOLDER_NAME_DUPLICATED: HTTPStatus.CONFLICT,
    ErrorCode.FOLDER_INVALID_PARENT: HTTPStatus.BAD_REQUEST,
    ErrorCode.TEMPLATE_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.TEMPLATE_NAME_DUPLICATED: HTTPStatus.CONFLICT,
    ErrorCode.TEMPLATE_SCOPE_FORBIDDEN: HTTPStatus.FORBIDDEN,
    ErrorCode.KB_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.KB_NAME_DUPLICATED: HTTPStatus.CONFLICT,
    ErrorCode.KB_PERMISSION_DENIED: HTTPStatus.FORBIDDEN,
    ErrorCode.KB_STATUS_CONFLICT: HTTPStatus.CONFLICT,
    ErrorCode.KB_HAS_RUNNING_IMPORT: HTTPStatus.CONFLICT,
    ErrorCode.FILE_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.FILE_TYPE_UNSUPPORTED: HTTPStatus.BAD_REQUEST,
    ErrorCode.FILE_SIZE_EXCEEDED: HTTPStatus.BAD_REQUEST,
    ErrorCode.FILE_DUPLICATED: HTTPStatus.CONFLICT,
    ErrorCode.FILE_IN_USE: HTTPStatus.CONFLICT,
    ErrorCode.IMPORT_JOB_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.IMPORT_INVALID_OPTIONS: HTTPStatus.BAD_REQUEST,
    ErrorCode.IMPORT_CONCURRENCY_LIMIT: HTTPStatus.CONFLICT,
    ErrorCode.IMPORT_PARSE_FAILED: HTTPStatus.INTERNAL_SERVER_ERROR,
    ErrorCode.IMPORT_CHUNK_FAILED: HTTPStatus.INTERNAL_SERVER_ERROR,
    ErrorCode.IMPORT_EMBEDDING_FAILED: HTTPStatus.INTERNAL_SERVER_ERROR,
    ErrorCode.IMPORT_INDEX_FAILED: HTTPStatus.INTERNAL_SERVER_ERROR,
    ErrorCode.HIT_TEST_EMPTY_QUERY: HTTPStatus.BAD_REQUEST,
    ErrorCode.HIT_TEST_INVALID_TOPK: HTTPStatus.BAD_REQUEST,
    ErrorCode.HIT_TEST_INDEX_NOT_READY: HTTPStatus.CONFLICT,
    ErrorCode.VALIDATION_ERROR: HTTPStatus.UNPROCESSABLE_ENTITY,
    ErrorCode.RESOURCE_NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.IDEMPOTENCY_CONFLICT: HTTPStatus.CONFLICT,
    ErrorCode.RATE_LIMITED: HTTPStatus.TOO_MANY_REQUESTS,
    ErrorCode.INTERNAL_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,
}


def resolve_error_code(code: ErrorCode | str) -> ErrorCode:
    if isinstance(code, ErrorCode):
        return code
    try:
        return ErrorCode(code)
    except ValueError as exc:
        raise ValueError(f"Unknown error code: {code}") from exc


def http_status_for_code(code: ErrorCode | str, *, override: int | None = None) -> int:
    if override is not None:
        return override
    resolved = resolve_error_code(code)
    return ERROR_HTTP_STATUS.get(resolved, HTTPStatus.INTERNAL_SERVER_ERROR)


def default_message_for_code(code: ErrorCode | str) -> str:
    resolved = resolve_error_code(code)
    return DEFAULT_ERROR_MESSAGES.get(resolved, "Request failed")


class BusinessError(Exception):
    """可预期的业务异常，由全局处理器转换为统一错误响应。"""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.code = resolve_error_code(code)
        self.message = message or default_message_for_code(self.code)
        self.details = details or {}
        self.status_code = status_code
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return http_status_for_code(self.code, override=self.status_code)
