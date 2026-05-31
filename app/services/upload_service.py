from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.core.errors import BusinessError, ErrorCode
from app.domain.upload import (
    DEFAULT_LOCAL_DEV_UPLOAD_URL_BASE,
    DEFAULT_PRESIGN_TTL_SECONDS,
    MAX_UPLOAD_FILES_PER_REQUEST,
    PresignFileInput,
    UploadObjectStatus,
    build_local_dev_upload_url,
    build_storage_key,
    is_allowed_file_size,
    is_allowed_file_type,
    presign_expires_at,
    utc_now,
)
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.upload_repository import UploadRepository
from app.services.rag.pipeline import RagPipeline


@dataclass(slots=True)
class PresignUploadResult:
    upload_id: str
    file_id: str
    method: str
    upload_url: str
    headers: dict[str, str]
    storage_key: str
    expires_at: datetime
    replaced: bool = False

    def to_api_dict(self) -> dict[str, object]:
        return {
            "uploadId": self.upload_id,
            "fileId": self.file_id,
            "method": self.method,
            "uploadUrl": self.upload_url,
            "headers": self.headers,
            "storageKey": self.storage_key,
            "expiresAt": self.expires_at.isoformat().replace("+00:00", "Z"),
            "replaced": self.replaced,
        }


@dataclass(slots=True)
class CompleteUploadResult:
    file_id: str
    status: str

    def to_api_dict(self) -> dict[str, object]:
        return {
            "fileId": self.file_id,
            "status": self.status,
        }


class UploadService:
    """上传用例服务：presign 与 complete 底层能力。"""

    def __init__(
        self,
        *,
        settings: Settings,
        upload_repository: UploadRepository,
        knowledge_base_repository: KnowledgeBaseRepository | None = None,
        rag_pipeline: RagPipeline | None = None,
        dev_upload_url_base: str = DEFAULT_LOCAL_DEV_UPLOAD_URL_BASE,
        presign_ttl_seconds: int = DEFAULT_PRESIGN_TTL_SECONDS,
    ) -> None:
        self._settings = settings
        self._upload_repository = upload_repository
        self._knowledge_base_repository = knowledge_base_repository
        self._rag_pipeline = rag_pipeline
        self._dev_upload_url_base = dev_upload_url_base.rstrip("/")
        self._presign_ttl_seconds = presign_ttl_seconds

    def presign(
        self,
        *,
        knowledge_base_id: str,
        files: list[PresignFileInput],
        user_id: str,
    ) -> list[PresignUploadResult]:
        if not files:
            raise BusinessError(
                ErrorCode.VALIDATION_ERROR,
                message="At least one file is required",
                details={"field": "files"},
            )
        if len(files) > MAX_UPLOAD_FILES_PER_REQUEST:
            raise BusinessError(
                ErrorCode.VALIDATION_ERROR,
                message=f"No more than {MAX_UPLOAD_FILES_PER_REQUEST} files per request",
                details={"maxFiles": MAX_UPLOAD_FILES_PER_REQUEST, "count": len(files)},
            )

        self._require_knowledge_base(knowledge_base_id)
        kb_name = self._knowledge_base_name(knowledge_base_id)
        self._upload_repository.ensure_knowledge_base_stub(
            knowledge_base_id,
            name=kb_name,
        )

        results: list[PresignUploadResult] = []
        for file_input in files:
            self._validate_file_input(file_input)
            existing = self._upload_repository.get_kb_file_by_name(
                knowledge_base_id,
                file_input.file_name,
            )
            replaced = existing is not None
            if replaced:
                file_id = existing.id
                storage_key = existing.storage_key
            else:
                file_id = f"file_{uuid4().hex}"
                storage_key = build_storage_key(
                    knowledge_base_id,
                    file_id,
                    file_input.file_name,
                )
            upload_id = f"upl_{uuid4().hex}"
            upload_url = build_local_dev_upload_url(
                upload_id,
                base_url=self._dev_upload_url_base,
            )
            expires_at = presign_expires_at(ttl_seconds=self._presign_ttl_seconds)

            self._prepare_local_storage_path(storage_key)
            if replaced:
                self._upload_repository.delete_upload_sessions_for_storage_key(
                    storage_key
                )

            upload = self._upload_repository.create_upload_object(
                kb_id=knowledge_base_id,
                user_id=user_id,
                file_id=file_id,
                file_name=file_input.file_name,
                mime_type=file_input.mime_type,
                size_bytes=file_input.size_bytes,
                storage_key=storage_key,
                upload_url=upload_url,
                expires_at=expires_at,
                upload_id=upload_id,
            )

            results.append(
                PresignUploadResult(
                    upload_id=upload.id,
                    file_id=upload.file_id,
                    method="PUT",
                    upload_url=upload_url,
                    headers={"Content-Type": file_input.mime_type},
                    storage_key=storage_key,
                    expires_at=expires_at,
                    replaced=replaced,
                )
            )

        return results

    def complete(
        self,
        *,
        upload_id: str,
        file_id: str,
        storage_key: str,
        user_id: str,
        etag: str | None = None,
    ) -> CompleteUploadResult:
        upload = self._upload_repository.get_upload_object(upload_id)
        if upload is None:
            raise BusinessError(
                ErrorCode.RESOURCE_NOT_FOUND,
                details={"uploadId": upload_id},
            )

        if upload.user_id != user_id:
            raise BusinessError(ErrorCode.KB_PERMISSION_DENIED)

        if upload.status == UploadObjectStatus.UPLOADED:
            existing = self._upload_repository.get_kb_file(upload.file_id)
            if existing is not None:
                return CompleteUploadResult(file_id=existing.id, status=existing.status)

        if upload.status not in {UploadObjectStatus.PENDING, UploadObjectStatus.UPLOADING}:
            raise BusinessError(
                ErrorCode.KB_STATUS_CONFLICT,
                message="Upload session is not available for completion",
                details={"uploadId": upload_id, "status": upload.status.value},
            )

        if upload.expires_at < utc_now():
            raise BusinessError(
                ErrorCode.VALIDATION_ERROR,
                message="Upload session has expired",
                details={"uploadId": upload_id, "expiresAt": upload.expires_at.isoformat()},
            )

        if upload.file_id != file_id:
            raise BusinessError(
                ErrorCode.VALIDATION_ERROR,
                message="fileId does not match upload session",
                details={"uploadId": upload_id, "fileId": file_id},
            )

        if upload.storage_key != storage_key:
            raise BusinessError(
                ErrorCode.VALIDATION_ERROR,
                message="storageKey does not match upload session",
                details={"uploadId": upload_id, "storageKey": storage_key},
            )

        existing_file = self._upload_repository.get_kb_file(file_id)
        if existing_file is None:
            self._upload_repository.create_kb_file(
                kb_id=upload.kb_id,
                file_id=file_id,
                file_name=upload.file_name,
                mime_type=upload.mime_type,
                size_bytes=upload.size_bytes,
                storage_key=storage_key,
                status="uploaded",
            )
        else:
            self._upload_repository.update_kb_file(
                file_id=file_id,
                mime_type=upload.mime_type,
                size_bytes=upload.size_bytes,
                status="uploaded",
            )
            self._clear_file_index(upload.kb_id, file_id)
            self._remove_stale_enhanced_markdown(storage_key)

        self._ensure_local_upload_placeholder(storage_key)

        updated = self._upload_repository.mark_upload_object_uploaded(
            upload_id,
            etag=etag,
        )
        if updated is None:
            raise BusinessError(
                ErrorCode.INTERNAL_ERROR,
                message="Failed to finalize upload session",
                details={"uploadId": upload_id},
            )

        return CompleteUploadResult(file_id=file_id, status="uploaded")

    def _require_knowledge_base(self, knowledge_base_id: str) -> None:
        if self._knowledge_base_repository is None:
            return
        if self._knowledge_base_repository.get_by_id(knowledge_base_id) is None:
            raise BusinessError(
                ErrorCode.KB_NOT_FOUND,
                details={"knowledgeBaseId": knowledge_base_id},
            )

    def _knowledge_base_name(self, knowledge_base_id: str) -> str | None:
        if self._knowledge_base_repository is None:
            return None
        metadata = self._knowledge_base_repository.get_by_id(knowledge_base_id)
        if metadata is None:
            return None
        return metadata.name

    @staticmethod
    def _validate_file_input(file_input: PresignFileInput) -> None:
        file_name = file_input.file_name.strip()
        if not file_name:
            raise BusinessError(
                ErrorCode.VALIDATION_ERROR,
                message="fileName must not be empty",
                details={"field": "fileName"},
            )

        if not is_allowed_file_type(
            file_name=file_name,
            mime_type=file_input.mime_type,
        ):
            raise BusinessError(
                ErrorCode.FILE_TYPE_UNSUPPORTED,
                details={
                    "fileName": file_name,
                    "mimeType": file_input.mime_type,
                },
            )

        if not is_allowed_file_size(file_input.size_bytes):
            raise BusinessError(
                ErrorCode.FILE_SIZE_EXCEEDED,
                details={
                    "fileName": file_name,
                    "sizeBytes": file_input.size_bytes,
                    "maxBytes": 20 * 1024 * 1024,
                },
            )

    def _prepare_local_storage_path(self, storage_key: str) -> None:
        destination = self._local_storage_path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)

    def _ensure_local_upload_placeholder(self, storage_key: str) -> None:
        destination = self._local_storage_path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(b"")

    def _local_storage_path(self, storage_key: str) -> Path:
        relative = Path(storage_key)
        if relative.is_absolute() or ".." in relative.parts:
            raise BusinessError(
                ErrorCode.VALIDATION_ERROR,
                message="Invalid storage key",
                details={"storageKey": storage_key},
            )
        return self._settings.upload_root_resolved / relative

    def _clear_file_index(self, kb_id: str, file_id: str) -> None:
        if self._rag_pipeline is None:
            return
        self._rag_pipeline.clear_file_index(kb_id, file_id)

    def _remove_stale_enhanced_markdown(self, storage_key: str) -> None:
        path = Path(storage_key)
        if path.suffix.lower() != ".pdf":
            return
        markdown_path = self._local_storage_path(path.with_suffix(".md").as_posix())
        if markdown_path.exists():
            markdown_path.unlink()
