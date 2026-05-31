from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.errors import BusinessError, ErrorCode
from app.domain.knowledge_base import KnowledgeBaseMetadata
from app.domain.upload import (
    PresignFileInput,
    build_local_dev_upload_url,
    build_storage_key,
    is_allowed_file_size,
    is_allowed_file_type,
)
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.upload_repository import UploadRepository
from app.services.rag.pipeline import RagPipeline
from app.services.upload_service import UploadService


@pytest.fixture
def upload_repo(database_url: str, db_connection) -> UploadRepository:
    repo = UploadRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return repo


@pytest.fixture
def upload_service(tmp_path: Path, upload_repo: UploadRepository) -> UploadService:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    settings = Settings(LOCAL_UPLOAD_ROOT=str(upload_root))
    return UploadService(
        settings=settings,
        upload_repository=upload_repo,
        dev_upload_url_base="http://127.0.0.1:8000",
    )


def test_file_type_and_size_validation() -> None:
    assert is_allowed_file_type(
        file_name="report.pdf",
        mime_type="application/pdf",
    )
    assert not is_allowed_file_type(
        file_name="script.exe",
        mime_type="application/octet-stream",
    )
    assert is_allowed_file_size(1024)
    assert not is_allowed_file_size(0)
    assert not is_allowed_file_size(21 * 1024 * 1024)


def test_build_storage_key_and_local_upload_url() -> None:
    storage_key = build_storage_key("kb_123", "file_abc", "a.pdf")
    assert storage_key == "kb/kb_123/file_abc.pdf"
    assert (
        build_local_dev_upload_url("upl_test")
        == "http://127.0.0.1:8000/v1/_dev/uploads/upl_test"
    )


def test_presign_and_complete_flow(upload_service: UploadService) -> None:
    kb_id = f"kb_{uuid4().hex[:8]}"
    user_id = f"user_{uuid4().hex[:8]}"

    results = upload_service.presign(
        knowledge_base_id=kb_id,
        user_id=user_id,
        files=[
            PresignFileInput(
                file_name="notes.pdf",
                mime_type="application/pdf",
                size_bytes=2048,
            )
        ],
    )

    assert len(results) == 1
    item = results[0]
    assert item.method == "PUT"
    assert item.upload_url.startswith("http://127.0.0.1:8000/v1/_dev/uploads/upl_")
    assert item.storage_key == build_storage_key(kb_id, item.file_id, "notes.pdf")
    assert item.headers == {"Content-Type": "application/pdf"}

    completed = upload_service.complete(
        upload_id=item.upload_id,
        file_id=item.file_id,
        storage_key=item.storage_key,
        user_id=user_id,
        etag="etag-demo",
    )
    assert completed.file_id == item.file_id
    assert completed.status == "uploaded"

    local_path = upload_service._local_storage_path(item.storage_key)
    assert local_path.exists()


def test_presign_replaces_existing_file_by_name(
    upload_service: UploadService,
) -> None:
    kb_id = f"kb_{uuid4().hex[:8]}"
    user_id = f"user_{uuid4().hex[:8]}"
    file_input = PresignFileInput(
        file_name="manual.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
    )

    first = upload_service.presign(
        knowledge_base_id=kb_id,
        user_id=user_id,
        files=[file_input],
    )[0]
    upload_service.complete(
        upload_id=first.upload_id,
        file_id=first.file_id,
        storage_key=first.storage_key,
        user_id=user_id,
    )

    second = upload_service.presign(
        knowledge_base_id=kb_id,
        user_id=user_id,
        files=[
            PresignFileInput(
                file_name="manual.pdf",
                mime_type="application/pdf",
                size_bytes=2048,
            )
        ],
    )[0]

    assert second.replaced is True
    assert second.file_id == first.file_id
    assert second.storage_key == first.storage_key

    completed = upload_service.complete(
        upload_id=second.upload_id,
        file_id=second.file_id,
        storage_key=second.storage_key,
        user_id=user_id,
    )
    assert completed.file_id == first.file_id


def test_presign_rejects_unsupported_type(upload_service: UploadService) -> None:
    with pytest.raises(BusinessError) as exc_info:
        upload_service.presign(
            knowledge_base_id="kb_test",
            user_id="user_test",
            files=[
                PresignFileInput(
                    file_name="virus.exe",
                    mime_type="application/octet-stream",
                    size_bytes=100,
                )
            ],
        )
    assert exc_info.value.code == ErrorCode.FILE_TYPE_UNSUPPORTED


def test_presign_rejects_oversized_file(upload_service: UploadService) -> None:
    with pytest.raises(BusinessError) as exc_info:
        upload_service.presign(
            knowledge_base_id="kb_test",
            user_id="user_test",
            files=[
                PresignFileInput(
                    file_name="large.pdf",
                    mime_type="application/pdf",
                    size_bytes=21 * 1024 * 1024,
                )
            ],
        )
    assert exc_info.value.code == ErrorCode.FILE_SIZE_EXCEEDED


def test_presign_validates_knowledge_base(
    tmp_path: Path,
    upload_repo: UploadRepository,
) -> None:
    metadata_path = tmp_path / "knowledge_bases.json"
    metadata_path.write_text('{"knowledge_bases": {}}', encoding="utf-8")
    kb_repo = KnowledgeBaseRepository(metadata_path)
    kb = KnowledgeBaseMetadata(name="demo-kb")
    kb.backend_refs["api_id"] = "kb_demo"
    kb_repo.save(kb)

    service = UploadService(
        settings=Settings(LOCAL_UPLOAD_ROOT=str(tmp_path / "uploads")),
        upload_repository=upload_repo,
        knowledge_base_repository=kb_repo,
    )

    with pytest.raises(BusinessError) as exc_info:
        service.presign(
            knowledge_base_id="kb_missing",
            user_id="user_test",
            files=[
                PresignFileInput(
                    file_name="notes.pdf",
                    mime_type="application/pdf",
                    size_bytes=100,
                )
            ],
        )
    assert exc_info.value.code == ErrorCode.KB_NOT_FOUND

    results = service.presign(
        knowledge_base_id="kb_demo",
        user_id="user_test",
        files=[
            PresignFileInput(
                file_name="notes.pdf",
                mime_type="application/pdf",
                size_bytes=100,
            )
        ],
    )
    assert len(results) == 1
