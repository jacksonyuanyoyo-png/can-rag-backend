from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.errors import BusinessError, ErrorCode
from app.domain.upload import KnowledgeBaseFileRecord, utc_now
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.services.kb_source_file import content_disposition_header, resolve_kb_source_file
from app.services.knowledge_base_adapter import create_kb
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.rag.pipeline import RagPipeline


def test_content_disposition_supports_non_ascii_filename() -> None:
    header = content_disposition_header(
        disposition="inline",
        file_name="测试文档_RAG介绍.docx",
    )
    header.encode("latin-1")
    assert "filename*=" in header
    assert "UTF-8" in header
    assert "%" in header


def test_resolve_pg_source_ignores_enhanced_markdown_beside_pdf(
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    file_id = "file_abc"
    metadata_path = tmp_path / "knowledge_bases.json"
    metadata_path.write_text('{"knowledge_bases": {}}', encoding="utf-8")
    settings = Settings(
        LOCAL_METADATA_PATH=str(metadata_path),
        LOCAL_UPLOAD_ROOT=str(upload_root),
        DATABASE_URL="",
        RAG_BACKEND="local",
    )
    repository = KnowledgeBaseRepository(settings.metadata_path_resolved)
    service = KnowledgeBaseService(
        settings=settings,
        repository=repository,
        rag_pipeline=RagPipeline(settings=settings),
    )
    metadata = create_kb(service, name="source-kb")
    storage_key = f"kb/{metadata.id}/{file_id}.pdf"
    pdf_path = upload_root / storage_key
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 original")
    pdf_path.with_suffix(".md").write_text("# enhanced", encoding="utf-8")

    now = utc_now()
    record = KnowledgeBaseFileRecord(
        id=file_id,
        kb_id=metadata.id,
        file_name="report.pdf",
        mime_type="application/pdf",
        size_bytes=8,
        storage_key=storage_key,
        status="ready",
        created_at=now,
        updated_at=now,
    )

    resolved = resolve_kb_source_file(
        settings=settings,
        metadata=metadata,
        file_id=file_id,
        pg_record=record,
        document=None,
    )

    assert resolved.path == pdf_path
    assert resolved.mime_type == "application/pdf"
    assert resolved.storage_key == storage_key


def test_resolve_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    metadata_path = tmp_path / "knowledge_bases.json"
    metadata_path.write_text('{"knowledge_bases": {}}', encoding="utf-8")
    settings = Settings(
        LOCAL_METADATA_PATH=str(metadata_path),
        LOCAL_UPLOAD_ROOT=str(tmp_path / "uploads"),
        DATABASE_URL="",
        RAG_BACKEND="local",
    )
    repository = KnowledgeBaseRepository(settings.metadata_path_resolved)
    service = KnowledgeBaseService(
        settings=settings,
        repository=repository,
        rag_pipeline=RagPipeline(settings=settings),
    )
    metadata = create_kb(service, name="empty-kb")

    with pytest.raises(BusinessError) as exc_info:
        resolve_kb_source_file(
            settings=settings,
            metadata=metadata,
            file_id="file_missing",
            pg_record=None,
            document=None,
        )
    assert exc_info.value.code == ErrorCode.FILE_NOT_FOUND
