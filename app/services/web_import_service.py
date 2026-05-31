from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.core.errors import BusinessError, ErrorCode
from app.domain.upload import build_storage_key
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.upload_repository import UploadRepository
from app.services.import_job_service import ImportJobCreateRequest, ImportJobService
from app.services.rag.parsing.web_extractor import extract_from_url, url_to_base_filename
from app.services.rag.parsing.web_fetcher import WebFetchError, validate_web_url


@dataclass(slots=True)
class WebImportResult:
    file_id: str
    file_name: str
    storage_key: str
    source_url: str
    extraction_method: str
    import_job_id: str | None = None

    def to_api_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "fileId": self.file_id,
            "fileName": self.file_name,
            "storageKey": self.storage_key,
            "sourceUrl": self.source_url,
            "extractionMethod": self.extraction_method,
        }
        if self.import_job_id is not None:
            payload["importJobId"] = self.import_job_id
        return payload


class WebImportService:
    """从网页 URL 抽取正文、落盘为 Markdown 并注册知识库文件。"""

    def __init__(
        self,
        *,
        settings: Settings,
        upload_repository: UploadRepository,
        knowledge_base_repository: KnowledgeBaseRepository | None = None,
        import_job_service: ImportJobService | None = None,
    ) -> None:
        self._settings = settings
        self._upload_repository = upload_repository
        self._knowledge_base_repository = knowledge_base_repository
        self._import_job_service = import_job_service

    def import_url(
        self,
        *,
        knowledge_base_id: str,
        url: str,
        user_id: str,
        use_browser_fallback: bool | None = None,
        auto_import: bool = True,
        chunking: object | None = None,
        chunk_strategy: str = "default",
        meta_filename: bool = True,
        meta_headings: bool = True,
        idempotency_key: str | None = None,
    ) -> WebImportResult:
        self._require_knowledge_base(knowledge_base_id)
        self._upload_repository.ensure_knowledge_base_stub(
            knowledge_base_id,
            name=self._knowledge_base_name(knowledge_base_id),
        )

        try:
            validated_url = validate_web_url(url)
            extraction = extract_from_url(
                validated_url,
                settings=self._settings,
                use_browser_fallback=use_browser_fallback,
            )
        except WebFetchError as exc:
            raise BusinessError(
                ErrorCode.IMPORT_PARSE_FAILED,
                message=str(exc),
                details={"url": url},
            ) from exc

        file_id = f"file_{uuid4().hex}"
        file_name = self._resolve_unique_file_name(
            knowledge_base_id,
            url_to_base_filename(extraction.source_url, extraction.title),
        )
        storage_key = build_storage_key(knowledge_base_id, file_id, file_name)
        markdown_bytes = self._build_stored_markdown(extraction.markdown, extraction.source_url)
        self._write_storage_file(storage_key, markdown_bytes)

        self._upload_repository.create_kb_file(
            kb_id=knowledge_base_id,
            file_id=file_id,
            file_name=file_name,
            mime_type="text/markdown",
            size_bytes=len(markdown_bytes),
            storage_key=storage_key,
            status="uploaded",
        )

        import_job_id: str | None = None
        if auto_import and self._import_job_service is not None:
            from app.domain.import_job import ChunkingConfig

            if chunking is not None and isinstance(chunking, ChunkingConfig):
                chunking_config = chunking
            else:
                chunking_config = ChunkingConfig.default(
                    chunk_strategy,
                    meta_filename=meta_filename,
                    meta_headings=meta_headings,
                )
            job_result = self._import_job_service.create(
                ImportJobCreateRequest(
                    kb_id=knowledge_base_id,
                    file_ids=[file_id],
                    chunk_strategy=chunking_config.strategy,
                    meta_filename=chunking_config.meta_filename,
                    meta_headings=chunking_config.meta_headings,
                    chunking=chunking_config,
                ),
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            import_job_id = job_result.job.id

        return WebImportResult(
            file_id=file_id,
            file_name=file_name,
            storage_key=storage_key,
            source_url=extraction.source_url,
            extraction_method=extraction.method,
            import_job_id=import_job_id,
        )

    def _resolve_unique_file_name(self, kb_id: str, base_name: str) -> str:
        candidate = base_name
        stem = Path(base_name).stem
        suffix = Path(base_name).suffix or ".md"
        counter = 2
        while self._upload_repository.get_kb_file_by_name(kb_id, candidate):
            candidate = f"{stem}-{counter}{suffix}"
            counter += 1
        return candidate

    @staticmethod
    def _build_stored_markdown(body: str, source_url: str) -> bytes:
        fetched_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        header = f"<!-- can-rag-source: {source_url} fetched-at: {fetched_at} -->\n\n"
        return (header + body.strip() + "\n").encode("utf-8")

    def _write_storage_file(self, storage_key: str, content: bytes) -> None:
        destination = self._local_storage_path(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    def _local_storage_path(self, storage_key: str) -> Path:
        relative = Path(storage_key)
        if relative.is_absolute() or ".." in relative.parts:
            raise BusinessError(
                ErrorCode.VALIDATION_ERROR,
                message="Invalid storage key",
                details={"storageKey": storage_key},
            )
        return self._settings.upload_root_resolved / relative

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
