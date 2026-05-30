from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any


class UploadObjectStatus(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


MAX_UPLOAD_FILE_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_FILES_PER_REQUEST = 100
DEFAULT_PRESIGN_TTL_SECONDS = 15 * 60
DEFAULT_LOCAL_DEV_UPLOAD_URL_BASE = "http://127.0.0.1:8000"

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".txt",
        ".md",
        ".csv",
        ".xls",
        ".xlsx",
    }
)

EXTENSION_MIME_TYPES: dict[str, frozenset[str]] = {
    ".pdf": frozenset({"application/pdf"}),
    ".doc": frozenset({"application/msword"}),
    ".docx": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    ),
    ".ppt": frozenset({"application/vnd.ms-powerpoint"}),
    ".pptx": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
    ),
    ".txt": frozenset({"text/plain"}),
    ".md": frozenset({"text/markdown", "text/plain", "text/x-markdown"}),
    ".csv": frozenset({"text/csv", "application/csv", "text/plain"}),
    ".xls": frozenset({"application/vnd.ms-excel"}),
    ".xlsx": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
    ),
}

GENERIC_MIME_TYPES = frozenset(
    {
        "application/octet-stream",
        "",
    }
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_extension(file_name: str) -> str:
    return Path(file_name).suffix.lower()


def build_storage_key(kb_id: str, file_id: str, file_name: str) -> str:
    extension = normalize_extension(file_name)
    return f"kb/{kb_id}/{file_id}{extension}"


def build_local_dev_upload_url(
    upload_id: str,
    *,
    base_url: str = DEFAULT_LOCAL_DEV_UPLOAD_URL_BASE,
) -> str:
    return f"{base_url.rstrip('/')}/v1/_dev/uploads/{upload_id}"


def is_allowed_file_type(*, file_name: str, mime_type: str) -> bool:
    extension = normalize_extension(file_name)
    if extension not in ALLOWED_EXTENSIONS:
        return False

    normalized_mime = (mime_type or "").strip().lower()
    allowed_mimes = EXTENSION_MIME_TYPES.get(extension, frozenset())
    if normalized_mime in allowed_mimes or normalized_mime in GENERIC_MIME_TYPES:
        return True
    return False


def is_allowed_file_size(size_bytes: int) -> bool:
    return 0 < size_bytes <= MAX_UPLOAD_FILE_BYTES


@dataclass(slots=True)
class PresignFileInput:
    file_name: str
    mime_type: str
    size_bytes: int


@dataclass(slots=True)
class UploadObject:
    id: str
    kb_id: str
    user_id: str
    file_id: str
    file_name: str
    mime_type: str
    size_bytes: int
    storage_key: str
    status: UploadObjectStatus
    expires_at: datetime
    upload_url: str | None = None
    etag: str | None = None
    completed_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> UploadObject:
        return cls(
            id=str(row["id"]),
            kb_id=str(row["kb_id"]),
            user_id=str(row["user_id"]),
            file_id=str(row["file_id"]),
            file_name=str(row["file_name"]),
            mime_type=str(row["mime_type"]),
            size_bytes=int(row["size_bytes"]),
            storage_key=str(row["storage_key"]),
            upload_url=row.get("upload_url"),
            etag=row.get("etag"),
            status=UploadObjectStatus(str(row["status"])),
            expires_at=row["expires_at"],
            completed_at=row.get("completed_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(slots=True)
class KnowledgeBaseFileRecord:
    id: str
    kb_id: str
    file_name: str
    mime_type: str
    size_bytes: int
    storage_key: str
    status: str
    created_at: datetime
    updated_at: datetime
    char_count: int | None = None
    file_format: str | None = None
    tags: list[str] | None = None
    error_message: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> KnowledgeBaseFileRecord:
        raw_tags = row.get("tags")
        tags: list[str] | None = None
        if isinstance(raw_tags, list):
            tags = [str(item) for item in raw_tags]
        raw_char_count = row.get("char_count")
        return cls(
            id=str(row["id"]),
            kb_id=str(row["kb_id"]),
            file_name=str(row["file_name"]),
            mime_type=str(row["mime_type"]),
            size_bytes=int(row["size_bytes"]),
            storage_key=str(row["storage_key"]),
            status=str(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            char_count=int(raw_char_count) if raw_char_count is not None else None,
            file_format=str(row["file_format"]) if row.get("file_format") else None,
            tags=tags,
            error_message=str(row["error_message"]) if row.get("error_message") else None,
        )


def presign_expires_at(*, ttl_seconds: int = DEFAULT_PRESIGN_TTL_SECONDS) -> datetime:
    return utc_now() + timedelta(seconds=ttl_seconds)
