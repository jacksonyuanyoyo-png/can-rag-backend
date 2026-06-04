from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from app.core.config import Settings
from app.core.errors import BusinessError, ErrorCode
from app.domain.knowledge_base import DocumentMetadata, KnowledgeBaseMetadata
from app.domain.upload import KnowledgeBaseFileRecord


@dataclass(frozen=True, slots=True)
class ResolvedKbSourceFile:
    """知识库文件的落盘原文件（非 PDF 增强稿 .md）。"""

    path: Path
    file_name: str
    mime_type: str
    storage_key: str | None


def source_file_api_path(*, kb_id: str, file_id: str) -> str:
    return f"/v1/knowledge-bases/{kb_id}/files/{file_id}/raw"


def content_disposition_header(*, disposition: str, file_name: str) -> str:
    """生成可含中文等非 ASCII 文件名的 Content-Disposition（RFC 5987 filename*）。"""
    ascii_fallback = "".join(
        c if ord(c) < 128 and c not in ('"', "\\") else "_"
        for c in file_name
    ).strip() or "file"
    encoded = quote(file_name, safe="")
    return f'{disposition}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


def _local_path_for_storage_key(settings: Settings, storage_key: str) -> Path:
    relative = Path(storage_key)
    if relative.is_absolute() or ".." in relative.parts:
        raise BusinessError(
            ErrorCode.VALIDATION_ERROR,
            message="Invalid storage key",
            details={"storageKey": storage_key},
        )
    return settings.upload_root_resolved / relative


def _guess_mime_type(*, file_name: str, mime_type: str | None) -> str:
    if mime_type and mime_type.strip():
        return mime_type.strip()
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"


def resolve_kb_source_file(
    *,
    settings: Settings,
    metadata: KnowledgeBaseMetadata,
    file_id: str,
    pg_record: KnowledgeBaseFileRecord | None,
    document: DocumentMetadata | None,
) -> ResolvedKbSourceFile:
    """解析可预览/下载的源文件路径；绝不返回 PDF 增强生成的同目录 .md。"""
    if pg_record is not None and pg_record.kb_id == metadata.id:
        path = _local_path_for_storage_key(settings, pg_record.storage_key)
        if not path.is_file():
            raise BusinessError(
                ErrorCode.RESOURCE_NOT_FOUND,
                message="Source file is missing on disk",
                details={"fileId": file_id, "storageKey": pg_record.storage_key},
            )
        return ResolvedKbSourceFile(
            path=path,
            file_name=pg_record.file_name,
            mime_type=_guess_mime_type(
                file_name=pg_record.file_name,
                mime_type=pg_record.mime_type,
            ),
            storage_key=pg_record.storage_key,
        )

    if document is not None:
        safe_name = "".join(
            c if c.isalnum() or c in "._-" else "_" for c in metadata.name
        )
        path = settings.upload_root_resolved / safe_name / document.file_name
        if not path.is_file():
            raise BusinessError(
                ErrorCode.RESOURCE_NOT_FOUND,
                message="Source file is missing on disk",
                details={"fileId": file_id, "fileName": document.file_name},
            )
        content_type = document.content_type
        return ResolvedKbSourceFile(
            path=path,
            file_name=document.file_name,
            mime_type=_guess_mime_type(
                file_name=document.file_name,
                mime_type=content_type,
            ),
            storage_key=None,
        )

    raise BusinessError(
        ErrorCode.FILE_NOT_FOUND,
        details={"fileId": file_id},
    )
