from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.core.config import Settings
from app.core.errors import BusinessError, ErrorCode
from app.domain.knowledge_base import (
    BackendType,
    DocumentMetadata,
    EMBEDDING_MODEL_ID_KEY,
    KnowledgeBaseMetadata,
    RESOURCE_TYPE_KEY,
    SearchHit,
)
from app.domain.upload import KnowledgeBaseFileRecord
from app.repositories.kb_data_index_repository import KbDataIndexRepository
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.upload_repository import UploadRepository
from app.services.markdown_render import markdown_payload_for_storage_text
from app.services.rag.kb_embedding import KbEmbeddingConfig, resolve_kb_embedding_config
from app.services.rag.parsing.md_parser import extract_image_storage_keys
from app.services.rag.pipeline import RagPipeline

HIT_TEST_MIN_TOP_K = 1
HIT_TEST_MAX_TOP_K = 50
ResourceType = Literal["personal", "team"]
_PG_FILE_BUSY_STATUSES = frozenset({"parsing", "chunking", "indexing"})
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class KnowledgeBaseFileItem:
    id: str
    name: str
    format: str
    status: str
    char_count: int
    uploaded_at: str
    tags: list[str] | None = None

    def to_api_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "format": self.format,
            "status": self.status,
            "charCount": self.char_count,
            "uploadedAt": self.uploaded_at,
            "tags": self.tags,
        }


@dataclass(slots=True)
class KnowledgeBaseFileDetail:
    id: str
    name: str
    format: str
    status: str
    char_count: int
    uploaded_at: str
    tags: list[str] | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    error_message: str | None = None

    def to_api_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "format": self.format,
            "status": self.status,
            "charCount": self.char_count,
            "uploadedAt": self.uploaded_at,
            "tags": self.tags,
            "mimeType": self.mime_type,
            "sizeBytes": self.size_bytes,
            "errorMessage": self.error_message,
        }


@dataclass(slots=True)
class BatchDeleteFileFailure:
    file_id: str
    code: str
    message: str

    def to_api_dict(self) -> dict[str, str]:
        return {
            "fileId": self.file_id,
            "code": self.code,
            "message": self.message,
        }


@dataclass(slots=True)
class BatchDeleteFilesResult:
    succeeded: list[str]
    failed: list[BatchDeleteFileFailure]

    def to_api_dict(self) -> dict[str, object]:
        return {
            "succeeded": self.succeeded,
            "failed": [item.to_api_dict() for item in self.failed],
        }


@dataclass(slots=True)
class KnowledgeBaseIndexStats:
    status: str
    file_count: int
    chunk_count: int
    indexed_chunk_count: int
    failed_file_count: int
    last_indexed_at: str | None

    def to_api_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "fileCount": self.file_count,
            "chunkCount": self.chunk_count,
            "indexedChunkCount": self.indexed_chunk_count,
            "failedFileCount": self.failed_file_count,
            "lastIndexedAt": self.last_indexed_at,
        }


@dataclass(slots=True)
class PaginatedFiles:
    items: list[KnowledgeBaseFileItem]
    page: int
    page_size: int
    total: int

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


@dataclass(slots=True)
class FileChunkIndexItem:
    index_id: str
    text: str

    def to_api_dict(self) -> dict[str, object]:
        return {
            "indexId": self.index_id,
            "text": self.text,
        }


@dataclass(slots=True)
class FileChunkItem:
    data_id: str
    text: str
    char_count: int
    page: int | None
    chunk_index: int
    citation: dict[str, Any]
    indexes: list[FileChunkIndexItem]

    def to_api_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "dataId": self.data_id,
            "text": self.text,
            "charCount": self.char_count,
            "page": self.page,
            "chunkIndex": self.chunk_index,
            "citation": self.citation,
            "indexes": [item.to_api_dict() for item in self.indexes],
        }
        payload.update(markdown_payload_for_storage_text(self.text))
        return payload


@dataclass(slots=True)
class FileChunkContextItem:
    data_id: str
    chunk_index: int
    page: int | None
    text: str

    def to_api_dict(self) -> dict[str, object]:
        return {
            "dataId": self.data_id,
            "chunkIndex": self.chunk_index,
            "page": self.page,
            "text": self.text,
        }


@dataclass(slots=True)
class FileChunkWithContextResult:
    target: FileChunkItem
    before: list[FileChunkContextItem]
    after: list[FileChunkContextItem]

    def to_api_dict(self) -> dict[str, object]:
        return {
            "target": self.target.to_api_dict(),
            "context": {
                "before": [item.to_api_dict() for item in self.before],
                "after": [item.to_api_dict() for item in self.after],
            },
        }


class KnowledgeBaseService:
    """知识库用例服务。

    该层不直接暴露 HTTP 接口，负责协调元数据仓储、文档存储与 RAG 管线。
    """

    def __init__(
        self,
        *,
        settings: Settings,
        repository: KnowledgeBaseRepository,
        rag_pipeline: RagPipeline,
        upload_repository: UploadRepository | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._rag_pipeline = rag_pipeline
        self._upload_repository = upload_repository
        self._kb_data_index_repo: KbDataIndexRepository | None = None

    def attach_upload_repository(self, upload_repository: UploadRepository) -> None:
        self._upload_repository = upload_repository

    def resolve_file_count(
        self,
        kb_id: str,
        metadata: KnowledgeBaseMetadata | None = None,
    ) -> int:
        if self._upload_repository is not None:
            pg_stats = self._upload_repository.get_kb_aggregate_stats(kb_id)
            if pg_stats is not None:
                return int(pg_stats["file_count"])
            pg_count = self._upload_repository.count_kb_files(kb_id)
            if pg_count > 0:
                return pg_count
        if metadata is not None:
            return len(metadata.documents)
        resolved = self.find_kb_by_id(kb_id)
        return len(resolved.documents) if resolved is not None else 0

    def to_api_dict(self, metadata: KnowledgeBaseMetadata) -> dict[str, object]:
        from app.services.knowledge_base_adapter import get_resource_type

        embedding_model_id = metadata.backend_refs.get(EMBEDDING_MODEL_ID_KEY)
        return {
            "id": metadata.id,
            "name": metadata.name,
            "description": metadata.description,
            "fileCount": self.resolve_file_count(metadata.id, metadata),
            "resourceType": get_resource_type(metadata),
            "embeddingModelId": embedding_model_id if isinstance(embedding_model_id, str) else None,
            "updatedAt": metadata.updated_at,
        }

    def resolve_embedding_config(
        self,
        metadata: KnowledgeBaseMetadata | None = None,
        *,
        kb_id: str | None = None,
    ) -> KbEmbeddingConfig:
        resolved = metadata
        if resolved is None and kb_id:
            resolved = self.find_kb_by_id(kb_id)
        return resolve_kb_embedding_config(self._settings, resolved)

    def list_kbs(self) -> list[KnowledgeBaseMetadata]:
        return self._repository.list()

    def get_kb(self, name: str) -> KnowledgeBaseMetadata | None:
        return self._repository.get(name)

    def find_kb_by_id(self, kb_id: str) -> KnowledgeBaseMetadata | None:
        return self._repository.get_by_id(kb_id)

    def save_kb(self, metadata: KnowledgeBaseMetadata) -> KnowledgeBaseMetadata:
        return self._repository.save(metadata)

    def create_kb(
        self,
        *,
        name: str,
        backend: BackendType = BackendType.LOCAL,
        description: str = "",
    ) -> KnowledgeBaseMetadata:
        if self._repository.get(name) is not None:
            raise ValueError(f"知识库已存在: {name}")
        metadata = KnowledgeBaseMetadata(name=name, backend=backend, description=description)
        self._kb_dir(name).mkdir(parents=True, exist_ok=True)
        return self._repository.save(metadata)

    def delete_kb(self, name: str) -> None:
        self._repository.require(name)
        root = self._kb_dir(name)
        if root.exists():
            for child in root.iterdir():
                if child.is_file():
                    child.unlink()
            try:
                root.rmdir()
            except OSError:
                pass
        self._rag_pipeline.delete_knowledge_base(name)
        self._repository.delete(name)

    def update_description(self, *, name: str, description: str) -> KnowledgeBaseMetadata:
        metadata = self._repository.require(name)
        metadata.description = description
        return self._repository.save(metadata)

    def index_document(
        self,
        *,
        knowledge_base: str,
        file_name: str,
        content: bytes,
        content_type: str | None = None,
    ) -> KnowledgeBaseMetadata:
        metadata = self._repository.require(knowledge_base)
        destination = self._kb_dir(knowledge_base) / file_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        text = self._read_text(destination)
        document = self._rag_pipeline.index_document(
            knowledge_base=knowledge_base,
            file_name=file_name,
            text=text,
            content_type=content_type,
        )
        metadata.documents[document.document_id] = document
        return self._repository.save(metadata)

    def delete_document(self, *, knowledge_base: str, document_id: str) -> KnowledgeBaseMetadata:
        metadata = self._repository.require(knowledge_base)
        document = metadata.documents.pop(document_id, None)
        if document is None:
            raise ValueError(f"文档不存在: {document_id}")
        path = self._kb_dir(knowledge_base) / document.file_name
        if path.exists():
            path.unlink()
        self._rag_pipeline.delete_document(knowledge_base, document_id)
        return self._repository.save(metadata)

    def search(
        self,
        *,
        knowledge_base: str,
        query: str,
        top_k: int = 5,
        kb_id: str | None = None,
    ) -> list[SearchHit]:
        metadata = self._repository.require(knowledge_base)
        embedding_config = self.resolve_embedding_config(metadata)
        hits: list[SearchHit] | None = None
        if kb_id:
            hits = self._multi_vector_search(
                kb_id=kb_id,
                query=query,
                top_k=top_k,
                embedding_config=embedding_config,
            )
        if hits is None or not hits:
            hits = self._rag_pipeline.search(
                knowledge_base=knowledge_base,
                query=query,
                top_k=top_k,
            )
        return hits

    def hit_test(
        self,
        *,
        knowledge_base: str,
        query: str,
        top_k: int = 5,
        file_ids: list[str] | None = None,
        kb_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_query = query.strip()
        if not normalized_query:
            raise BusinessError(ErrorCode.HIT_TEST_EMPTY_QUERY)

        if top_k < HIT_TEST_MIN_TOP_K or top_k > HIT_TEST_MAX_TOP_K:
            raise BusinessError(
                ErrorCode.HIT_TEST_INVALID_TOPK,
                details={
                    "topK": top_k,
                    "min": HIT_TEST_MIN_TOP_K,
                    "max": HIT_TEST_MAX_TOP_K,
                },
            )

        metadata = self._repository.require(knowledge_base)
        if file_ids:
            for file_id in file_ids:
                if not self._file_exists(metadata=metadata, file_id=file_id):
                    raise BusinessError(
                        ErrorCode.FILE_NOT_FOUND,
                        details={"fileId": file_id},
                    )

        started_at = time.perf_counter()
        embedding_config = self.resolve_embedding_config(metadata)
        hits: list[SearchHit] | None = None
        if kb_id:
            hits = self._multi_vector_search(
                kb_id=kb_id,
                query=normalized_query,
                top_k=top_k,
                embedding_config=embedding_config,
            )
        if hits is None or not hits:
            hits = self._rag_pipeline.search(
                knowledge_base=knowledge_base,
                query=normalized_query,
                top_k=top_k,
            )
        latency_ms = int((time.perf_counter() - started_at) * 1000)

        if file_ids:
            allowed = set(file_ids)
            hits = [hit for hit in hits if hit.document_id in allowed]

        return {
            "results": [_search_hit_to_hit_test_result(hit) for hit in hits],
            "latencyMs": latency_ms,
        }

    def list_files(
        self,
        *,
        metadata: KnowledgeBaseMetadata,
        page: int = 1,
        page_size: int = 10,
        q: str | None = None,
        status: str | None = None,
        file_format: str | None = None,
    ) -> PaginatedFiles:
        if self._upload_repository is not None:
            pg_items = self._list_files_from_postgres(
                kb_id=metadata.id,
                q=q,
                status=status,
                file_format=file_format,
            )
            if pg_items or self._upload_repository.count_kb_files(metadata.id) > 0:
                safe_page = max(page, 1)
                safe_page_size = min(max(page_size, 1), 100)
                total = len(pg_items)
                start = (safe_page - 1) * safe_page_size
                end = start + safe_page_size
                return PaginatedFiles(
                    items=pg_items[start:end],
                    page=safe_page,
                    page_size=safe_page_size,
                    total=total,
                )

        chunk_counts = self._rag_pipeline.count_chunks_by_document(metadata.name)
        items = [
            self._to_file_item(
                metadata=metadata,
                document=document,
                chunk_count=chunk_counts.get(document.document_id, 0),
            )
            for document in metadata.documents.values()
        ]
        items.sort(key=lambda item: item.uploaded_at, reverse=True)

        if q:
            needle = q.strip().lower()
            items = [
                item
                for item in items
                if needle in item.name.lower() or needle in item.id.lower()
            ]
        if status:
            items = [item for item in items if item.status == status.strip().lower()]
        if file_format:
            items = [item for item in items if item.format == file_format.strip().lower()]

        safe_page = max(page, 1)
        safe_page_size = min(max(page_size, 1), 100)
        total = len(items)
        start = (safe_page - 1) * safe_page_size
        end = start + safe_page_size
        return PaginatedFiles(
            items=items[start:end],
            page=safe_page,
            page_size=safe_page_size,
            total=total,
        )

    def get_index_stats(self, *, metadata: KnowledgeBaseMetadata) -> KnowledgeBaseIndexStats:
        if self._upload_repository is not None:
            pg_stats = self._index_stats_from_postgres(metadata)
            if pg_stats is not None:
                return pg_stats

        chunk_counts = self._rag_pipeline.count_chunks_by_document(metadata.name)
        file_count = len(metadata.documents)
        chunk_count = self._rag_pipeline.count_chunks(metadata.name)
        failed_file_count = sum(
            1
            for document in metadata.documents.values()
            if chunk_counts.get(document.document_id, 0) == 0
        )
        indexed_chunk_count = chunk_count
        last_indexed_at = max(
            (document.updated_at for document in metadata.documents.values()),
            default=None,
        )
        if file_count == 0:
            kb_status = "ready"
        elif failed_file_count > 0:
            kb_status = "indexing"
        else:
            kb_status = "ready"
        return KnowledgeBaseIndexStats(
            status=kb_status,
            file_count=file_count,
            chunk_count=chunk_count,
            indexed_chunk_count=indexed_chunk_count,
            failed_file_count=failed_file_count,
            last_indexed_at=last_indexed_at,
        )

    def list_file_chunks(
        self,
        *,
        kb_id: str,
        file_id: str,
        page: int = 1,
        page_size: int = 10,
        q: str | None = None,
        status: str | None = None,
    ) -> tuple[list[FileChunkItem], int]:
        del status
        repo = self._kb_data_index_repository()
        rows = repo.list_data_by_file(kb_id, file_id)

        if q:
            needle = q.strip().casefold()
            if needle:
                rows = [row for row in rows if needle in str(row.get("text", "")).casefold()]

        safe_page = max(page, 1)
        safe_page_size = min(max(page_size, 1), 100)
        total = len(rows)
        start = (safe_page - 1) * safe_page_size
        page_rows = rows[start : start + safe_page_size]

        items: list[FileChunkItem] = []
        for row in page_rows:
            data_id = str(row["data_id"])
            text = str(row.get("text", ""))
            page_value = row.get("page")
            chunk_index = int(row.get("chunk_index", 0))
            citation_raw = row.get("citation")
            citation = dict(citation_raw) if isinstance(citation_raw, dict) else {}
            index_rows = repo.list_index_by_data(kb_id, file_id, data_id)
            indexes = [
                FileChunkIndexItem(
                    index_id=str(index_row["index_id"]),
                    text=str(index_row.get("text", "")),
                )
                for index_row in index_rows
            ]
            items.append(
                FileChunkItem(
                    data_id=data_id,
                    text=text,
                    char_count=len(text),
                    page=int(page_value) if isinstance(page_value, int) else None,
                    chunk_index=chunk_index,
                    citation=citation,
                    indexes=indexes,
                )
            )
        return items, total

    def get_file_chunk_with_context(
        self,
        *,
        kb_id: str,
        file_id: str,
        data_id: str,
        context: int = 1,
    ) -> FileChunkWithContextResult:
        repo = self._kb_data_index_repository()
        rows = repo.list_data_by_file(kb_id, file_id)

        target_idx: int | None = None
        for index, row in enumerate(rows):
            if str(row["data_id"]) == data_id:
                target_idx = index
                break
        if target_idx is None:
            raise BusinessError(
                ErrorCode.RESOURCE_NOT_FOUND,
                details={"dataId": data_id},
            )

        safe_context = min(max(context, 0), 10)
        before_rows = rows[max(0, target_idx - safe_context) : target_idx]
        after_rows = rows[target_idx + 1 : target_idx + 1 + safe_context]

        target_row = rows[target_idx]
        target_data_id = str(target_row["data_id"])
        target_text = str(target_row.get("text", ""))
        target_page = target_row.get("page")
        target_chunk_index = int(target_row.get("chunk_index", 0))
        citation_raw = target_row.get("citation")
        target_citation = dict(citation_raw) if isinstance(citation_raw, dict) else {}
        index_rows = repo.list_index_by_data(kb_id, file_id, target_data_id)
        target_indexes = [
            FileChunkIndexItem(
                index_id=str(index_row["index_id"]),
                text=str(index_row.get("text", "")),
            )
            for index_row in index_rows
        ]
        target = FileChunkItem(
            data_id=target_data_id,
            text=target_text,
            char_count=len(target_text),
            page=int(target_page) if isinstance(target_page, int) else None,
            chunk_index=target_chunk_index,
            citation=target_citation,
            indexes=target_indexes,
        )

        before = [self._row_to_context_item(row) for row in before_rows]
        after = [self._row_to_context_item(row) for row in after_rows]
        return FileChunkWithContextResult(target=target, before=before, after=after)

    def get_file_detail(
        self,
        *,
        metadata: KnowledgeBaseMetadata,
        file_id: str,
    ) -> KnowledgeBaseFileDetail:
        if self._upload_repository is not None:
            record = self._upload_repository.get_kb_file(file_id)
            if record is not None and record.kb_id == metadata.id:
                chunk_counts = self._pg_chunk_counts(metadata.id)
                return self._pg_record_to_file_detail(
                    record=record,
                    chunk_count=chunk_counts.get(file_id, 0),
                )

        document = self._require_document(metadata=metadata, file_id=file_id)
        chunk_counts = self._rag_pipeline.count_chunks_by_document(metadata.name)
        return self._to_file_detail(
            metadata=metadata,
            document=document,
            chunk_count=chunk_counts.get(document.document_id, 0),
        )

    def delete_file(
        self,
        *,
        metadata: KnowledgeBaseMetadata,
        file_id: str,
    ) -> dict[str, bool]:
        pg_record: KnowledgeBaseFileRecord | None = None
        if self._upload_repository is not None:
            candidate = self._upload_repository.get_kb_file(file_id)
            if candidate is not None and candidate.kb_id == metadata.id:
                pg_record = candidate

        document = metadata.documents.get(file_id)
        if pg_record is None and document is None:
            raise BusinessError(
                ErrorCode.FILE_NOT_FOUND,
                details={"fileId": file_id},
            )

        if document is not None:
            self._ensure_file_deletable(metadata=metadata, file_id=file_id)
        if pg_record is not None:
            self._ensure_pg_file_deletable(record=pg_record, document=document)

        if pg_record is not None:
            self._delete_pg_uploaded_file(metadata=metadata, record=pg_record)

        if document is not None and file_id in metadata.documents:
            self.delete_document(knowledge_base=metadata.name, document_id=file_id)
        elif pg_record is not None and file_id in metadata.documents:
            metadata.documents.pop(file_id, None)
            self._repository.save(metadata)

        return {"success": True}

    def batch_delete_files(
        self,
        *,
        metadata: KnowledgeBaseMetadata,
        file_ids: list[str],
    ) -> BatchDeleteFilesResult:
        succeeded: list[str] = []
        failed: list[BatchDeleteFileFailure] = []
        kb_name = metadata.name

        for file_id in file_ids:
            current = self._repository.require(kb_name)
            try:
                self.delete_file(metadata=current, file_id=file_id)
                succeeded.append(file_id)
            except BusinessError as exc:
                failed.append(
                    BatchDeleteFileFailure(
                        file_id=file_id,
                        code=exc.code.value,
                        message=exc.message,
                    )
                )

        return BatchDeleteFilesResult(succeeded=succeeded, failed=failed)

    def update_kb(
        self,
        *,
        metadata: KnowledgeBaseMetadata,
        name: str | None = None,
        description: str | None = None,
        resource_type: ResourceType | None = None,
    ) -> KnowledgeBaseMetadata:
        old_name = metadata.name
        new_name = name.strip() if name is not None else None

        if new_name is not None and new_name != old_name:
            if self._repository.get(new_name) is not None:
                raise BusinessError(
                    ErrorCode.KB_NAME_DUPLICATED,
                    details={"name": new_name},
                )
            self._rename_kb_assets(old_name=old_name, new_name=new_name)
            self._repository.delete(old_name)
            metadata.name = new_name

        if description is not None:
            metadata.description = description

        if resource_type is not None:
            metadata.backend_refs[RESOURCE_TYPE_KEY] = resource_type

        return self._repository.save(metadata)

    def _require_document(
        self,
        *,
        metadata: KnowledgeBaseMetadata,
        file_id: str,
    ) -> DocumentMetadata:
        document = metadata.documents.get(file_id)
        if document is None:
            raise BusinessError(
                ErrorCode.FILE_NOT_FOUND,
                details={"fileId": file_id},
            )
        return document

    def _file_exists(self, *, metadata: KnowledgeBaseMetadata, file_id: str) -> bool:
        if file_id in metadata.documents:
            return True
        if self._upload_repository is None:
            return False
        record = self._upload_repository.get_kb_file(file_id)
        return record is not None and record.kb_id == metadata.id

    def _ensure_file_deletable(
        self,
        *,
        metadata: KnowledgeBaseMetadata,
        file_id: str,
    ) -> DocumentMetadata:
        document = self._require_document(metadata=metadata, file_id=file_id)
        if self._is_file_in_use(document=document):
            raise BusinessError(
                ErrorCode.FILE_IN_USE,
                details={"fileId": file_id},
            )
        return document

    def _ensure_pg_file_deletable(
        self,
        *,
        record: KnowledgeBaseFileRecord,
        document: DocumentMetadata | None,
    ) -> None:
        if record.status in _PG_FILE_BUSY_STATUSES:
            raise BusinessError(
                ErrorCode.FILE_IN_USE,
                details={"fileId": record.id, "status": record.status},
            )
        if document is not None and self._is_file_in_use(document=document):
            raise BusinessError(
                ErrorCode.FILE_IN_USE,
                details={"fileId": record.id},
            )

    def _delete_pg_uploaded_file(
        self,
        *,
        metadata: KnowledgeBaseMetadata,
        record: KnowledgeBaseFileRecord,
    ) -> None:
        image_keys = self._collect_file_image_storage_keys(metadata.id, record.id)
        self._unlink_storage_key(record.storage_key)
        for key in image_keys:
            self._unlink_storage_key(key)

        self._rag_pipeline.clear_file_index(metadata.id, record.id)
        self._rag_pipeline.clear_file_index(metadata.name, record.id)

        try:
            self._kb_data_index_repository().delete_by_file(metadata.id, record.id)
        except Exception:
            logger.warning(
                "delete_by_file failed for kb=%s file=%s",
                metadata.id,
                record.id,
                exc_info=True,
            )

        assert self._upload_repository is not None
        self._upload_repository.delete_kb_file(kb_id=metadata.id, file_id=record.id)
        self._upload_repository.delete_upload_sessions_for_storage_key(record.storage_key)

    def _collect_file_image_storage_keys(self, kb_id: str, file_id: str) -> list[str]:
        try:
            rows = self._kb_data_index_repository().list_data_by_file(kb_id, file_id)
        except Exception:
            return []
        keys: list[str] = []
        for row in rows:
            keys.extend(extract_image_storage_keys(str(row.get("text", ""))))
            citation = row.get("citation") or {}
            storage_key = citation.get("storage_key") or citation.get("storageKey")
            if storage_key:
                keys.append(str(storage_key))
        seen: set[str] = set()
        ordered: list[str] = []
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
        return ordered

    def _unlink_storage_key(self, storage_key: str) -> None:
        relative = Path(storage_key)
        if relative.is_absolute() or ".." in relative.parts:
            return
        path = self._settings.upload_root_resolved / relative
        if path.is_file():
            path.unlink()
        if path.suffix.lower() == ".pdf":
            markdown_path = path.with_suffix(".md")
            if markdown_path.is_file():
                markdown_path.unlink()

    @staticmethod
    def _is_file_in_use(*, document: DocumentMetadata) -> bool:
        if document.backend_refs.get("in_use"):
            return True
        import_status = document.backend_refs.get("import_status")
        return import_status in {"pending", "running"}

    def _rename_kb_assets(self, *, old_name: str, new_name: str) -> None:
        old_dir = self._kb_dir(old_name)
        new_dir = self._kb_dir(new_name)
        if old_dir.exists():
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            if new_dir.exists():
                raise BusinessError(
                    ErrorCode.KB_NAME_DUPLICATED,
                    details={"name": new_name},
                )
            old_dir.rename(new_dir)

        old_vector = self._vector_store_path(old_name)
        new_vector = self._vector_store_path(new_name)
        if not old_vector.exists():
            return
        if new_vector.exists():
            raise BusinessError(
                ErrorCode.KB_NAME_DUPLICATED,
                details={"name": new_name},
            )

        payload = json.loads(old_vector.read_text(encoding="utf-8"))
        for item in payload.get("records", []):
            item["knowledge_base"] = new_name
        tmp = new_vector.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(new_vector)
        old_vector.unlink()

    def _vector_store_path(self, kb_name: str) -> Path:
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in kb_name)
        return self._settings.vector_store_path_resolved / f"{safe_name}.json"

    def _pg_chunk_counts(self, kb_id: str) -> dict[str, int]:
        try:
            return self._kb_data_index_repository().count_data_chunks_by_file(kb_id)
        except Exception:
            return {}

    def _list_files_from_postgres(
        self,
        *,
        kb_id: str,
        q: str | None,
        status: str | None,
        file_format: str | None,
    ) -> list[KnowledgeBaseFileItem]:
        assert self._upload_repository is not None
        records = self._upload_repository.list_kb_files(kb_id)
        chunk_counts = self._pg_chunk_counts(kb_id)
        items = [
            self._pg_record_to_file_item(
                record=record,
                chunk_count=chunk_counts.get(record.id, 0),
            )
            for record in records
        ]
        if q:
            needle = q.strip().lower()
            items = [
                item
                for item in items
                if needle in item.name.lower() or needle in item.id.lower()
            ]
        if status:
            items = [item for item in items if item.status == status.strip().lower()]
        if file_format:
            items = [item for item in items if item.format == file_format.strip().lower()]
        return items

    def _index_stats_from_postgres(
        self,
        metadata: KnowledgeBaseMetadata,
    ) -> KnowledgeBaseIndexStats | None:
        assert self._upload_repository is not None
        pg_stats = self._upload_repository.get_kb_aggregate_stats(metadata.id)
        file_count = self._upload_repository.count_kb_files(metadata.id)
        if pg_stats is None and file_count == 0:
            return None

        chunk_counts = self._pg_chunk_counts(metadata.id)
        records = self._upload_repository.list_kb_files(metadata.id)
        failed_file_count = sum(
            1 for record in records if chunk_counts.get(record.id, 0) == 0
        )
        chunk_count = int(pg_stats["chunk_count"]) if pg_stats is not None else sum(chunk_counts.values())
        if pg_stats is not None:
            file_count = int(pg_stats["file_count"])
        last_indexed_at = None
        if records:
            last_updated = max(record.updated_at for record in records)
            last_indexed_at = last_updated.isoformat().replace("+00:00", "Z")
        if file_count == 0:
            kb_status = "ready"
        elif failed_file_count > 0:
            kb_status = "indexing"
        else:
            kb_status = "ready"
        return KnowledgeBaseIndexStats(
            status=kb_status,
            file_count=file_count,
            chunk_count=chunk_count,
            indexed_chunk_count=chunk_count,
            failed_file_count=failed_file_count,
            last_indexed_at=last_indexed_at,
        )

    def _pg_record_to_file_item(
        self,
        *,
        record: KnowledgeBaseFileRecord,
        chunk_count: int,
    ) -> KnowledgeBaseFileItem:
        return KnowledgeBaseFileItem(
            id=record.id,
            name=record.file_name,
            format=record.file_format or self._file_format(record.file_name),
            status=self._pg_file_status(record=record, chunk_count=chunk_count),
            char_count=record.char_count if record.char_count is not None else record.size_bytes,
            uploaded_at=record.created_at.isoformat().replace("+00:00", "Z"),
            tags=record.tags,
        )

    def _pg_record_to_file_detail(
        self,
        *,
        record: KnowledgeBaseFileRecord,
        chunk_count: int,
    ) -> KnowledgeBaseFileDetail:
        return KnowledgeBaseFileDetail(
            id=record.id,
            name=record.file_name,
            format=record.file_format or self._file_format(record.file_name),
            status=self._pg_file_status(record=record, chunk_count=chunk_count),
            char_count=record.char_count if record.char_count is not None else record.size_bytes,
            uploaded_at=record.created_at.isoformat().replace("+00:00", "Z"),
            tags=record.tags,
            mime_type=record.mime_type,
            size_bytes=record.size_bytes,
            error_message=record.error_message,
        )

    @staticmethod
    def _pg_file_status(*, record: KnowledgeBaseFileRecord, chunk_count: int) -> str:
        if record.status == "failed":
            return "failed"
        if chunk_count > 0 or record.status == "ready":
            return "available"
        return "indexing"

    def _to_file_item(
        self,
        *,
        metadata: KnowledgeBaseMetadata,
        document: DocumentMetadata,
        chunk_count: int,
    ) -> KnowledgeBaseFileItem:
        path = self._kb_dir(metadata.name) / document.file_name
        char_count = len(self._read_text(path)) if path.exists() else 0
        file_status = "available" if chunk_count > 0 else "indexing"
        return KnowledgeBaseFileItem(
            id=document.document_id,
            name=document.file_name,
            format=self._file_format(document.file_name),
            status=file_status,
            char_count=char_count,
            uploaded_at=document.created_at,
            tags=None,
        )

    def _to_file_detail(
        self,
        *,
        metadata: KnowledgeBaseMetadata,
        document: DocumentMetadata,
        chunk_count: int,
    ) -> KnowledgeBaseFileDetail:
        path = self._kb_dir(metadata.name) / document.file_name
        char_count = len(self._read_text(path)) if path.exists() else 0
        file_status = "available" if chunk_count > 0 else "indexing"
        error_message = document.backend_refs.get("error_message")
        if not isinstance(error_message, str):
            error_message = None
        size_bytes = path.stat().st_size if path.exists() else None
        return KnowledgeBaseFileDetail(
            id=document.document_id,
            name=document.file_name,
            format=self._file_format(document.file_name),
            status=file_status,
            char_count=char_count,
            uploaded_at=document.created_at,
            tags=None,
            mime_type=document.content_type,
            size_bytes=size_bytes,
            error_message=error_message,
        )

    @staticmethod
    def _file_format(file_name: str) -> str:
        suffix = Path(file_name).suffix.lower().lstrip(".")
        return suffix or "unknown"

    @staticmethod
    def _row_to_context_item(row: dict[str, Any]) -> FileChunkContextItem:
        page_value = row.get("page")
        return FileChunkContextItem(
            data_id=str(row["data_id"]),
            chunk_index=int(row.get("chunk_index", 0)),
            page=int(page_value) if isinstance(page_value, int) else None,
            text=str(row.get("text", "")),
        )

    def _kb_data_index_repository(self) -> KbDataIndexRepository:
        if self._kb_data_index_repo is None:
            database_url = self._settings.DATABASE_URL.strip()
            if not database_url:
                raise RuntimeError("DATABASE_URL 未配置，无法查询知识库切片。")
            self._kb_data_index_repo = KbDataIndexRepository(database_url)
        return self._kb_data_index_repo

    def _kb_dir(self, kb_name: str) -> Path:
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in kb_name)
        return self._settings.upload_root_resolved / safe_name

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")

    def _multi_vector_search(
        self,
        *,
        kb_id: str,
        query: str,
        top_k: int,
        embedding_config: KbEmbeddingConfig,
    ) -> list[SearchHit] | None:
        try:
            return self._rag_pipeline.search_data(
                knowledge_base=kb_id,
                query=query,
                top_k=top_k,
                embedding_config=embedding_config,
            )
        except NotImplementedError:
            return None


def _search_hit_to_hit_test_result(hit: SearchHit) -> dict[str, Any]:
    page = hit.citation.get("page")
    return {
        "fileId": hit.document_id,
        "chunkId": hit.chunk_id,
        "score": hit.score,
        "snippet": hit.text,
        "page": int(page) if isinstance(page, int) else None,
    }
