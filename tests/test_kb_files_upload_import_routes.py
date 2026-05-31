from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.import_job import ImportJobStage, ImportJobStatus
from app.main import app
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.import_job_repository import ImportJobRepository
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.upload_repository import UploadRepository
from app.services.auth.auth_service import AuthService
from app.services.auth.refresh_store import InMemoryRefreshStore
from app.services.import_job_service import ImportJobService
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.rag.pipeline import RagPipeline
from app.services.upload_service import UploadService


@pytest.fixture(scope="session")
def db_schema_ready(database_url: str) -> None:
    UploadRepository(database_url).ensure_schema()
    ImportJobRepository(database_url).ensure_schema()
    IdempotencyRepository(database_url).ensure_schema()


def _login_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/v1/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
    )
    assert response.status_code == 200
    token = response.json()["data"]["accessToken"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def kb_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    metadata_path = tmp_path / "knowledge_bases.json"
    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps({"knowledge_bases": {}}), encoding="utf-8")

    settings = Settings(
        LOCAL_METADATA_PATH=str(metadata_path),
        LOCAL_UPLOAD_ROOT=str(upload_root),
        DATABASE_URL="",
        RAG_BACKEND="local",
    )
    repository = KnowledgeBaseRepository(settings.metadata_path_resolved)
    rag_pipeline = RagPipeline(settings=settings)
    kb_service = KnowledgeBaseService(
        settings=settings,
        repository=repository,
        rag_pipeline=rag_pipeline,
    )

    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)

    with TestClient(app) as test_client:
        test_client.app.state.settings = settings
        test_client.app.state.knowledge_base_service = kb_service
        test_client.app.state.auth_service = AuthService(
            settings=settings,
            refresh_store=InMemoryRefreshStore(),
        )
        test_client.app.state.database_status = {"status": "skipped"}
        test_client.app.state.upload_service = None
        test_client.app.state.import_job_service = None
        test_client.app.state.idempotency_repository = None
        yield test_client


@pytest.fixture
def domain_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    db_schema_ready: None,
) -> TestClient:
    metadata_path = tmp_path / "knowledge_bases.json"
    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps({"knowledge_bases": {}}), encoding="utf-8")

    settings = Settings(
        LOCAL_METADATA_PATH=str(metadata_path),
        LOCAL_UPLOAD_ROOT=str(upload_root),
        DATABASE_URL="",
        RAG_BACKEND="local",
    )
    repository = KnowledgeBaseRepository(settings.metadata_path_resolved)
    rag_pipeline = RagPipeline(settings=settings)
    kb_service = KnowledgeBaseService(
        settings=settings,
        repository=repository,
        rag_pipeline=rag_pipeline,
    )

    upload_settings = Settings(
        LOCAL_METADATA_PATH=str(metadata_path),
        LOCAL_UPLOAD_ROOT=str(upload_root),
        DATABASE_URL=database_url,
        RAG_BACKEND="local",
    )
    upload_repo = UploadRepository(database_url)
    kb_service.attach_upload_repository(upload_repo)
    upload_service = UploadService(
        settings=upload_settings,
        upload_repository=upload_repo,
        knowledge_base_repository=repository,
        rag_pipeline=rag_pipeline,
        dev_upload_url_base="http://testserver",
    )

    import_job_repo = ImportJobRepository(database_url)
    idempotency_repo = IdempotencyRepository(database_url)
    import_job_service = ImportJobService(
        import_job_repository=import_job_repo,
        idempotency_repository=idempotency_repo,
    )

    auth_service = AuthService(settings=settings, refresh_store=InMemoryRefreshStore())

    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)

    with TestClient(app) as test_client:
        test_client.app.state.settings = settings
        test_client.app.state.knowledge_base_service = kb_service
        test_client.app.state.auth_service = auth_service
        test_client.app.state.upload_service = upload_service
        test_client.app.state.import_job_service = import_job_service
        test_client.app.state.idempotency_repository = idempotency_repo
        test_client.app.state.database_status = {"status": "ok"}
        yield test_client


def _create_kb(client: TestClient, *, name: str | None = None) -> str:
    kb_name = name or f"kb-{uuid4().hex[:8]}"
    response = client.post(
        "/v1/knowledge-bases",
        json={"name": kb_name, "description": "demo"},
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


def _index_file(client: TestClient, kb_id: str, *, file_name: str, content: bytes) -> str:
    service: KnowledgeBaseService = client.app.state.knowledge_base_service
    metadata = service.find_kb_by_id(kb_id)
    assert metadata is not None
    saved = service.index_document(
        knowledge_base=metadata.name,
        file_name=file_name,
        content=content,
    )
    for document_id, document in saved.documents.items():
        if document.file_name == file_name:
            return document_id
    raise AssertionError(f"indexed file not found: {file_name}")


# --- KnowledgeBase ---


def test_create_and_get_knowledge_base_happy_path(kb_client: TestClient) -> None:
    kb_id = _create_kb(kb_client, name="happy-kb")

    response = kb_client.get(f"/v1/knowledge-bases/{kb_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["id"] == kb_id
    assert payload["data"]["name"] == "happy-kb"
    assert payload["requestId"]


def test_get_unknown_knowledge_base_returns_kb_not_found(kb_client: TestClient) -> None:
    response = kb_client.get("/v1/knowledge-bases/kb_missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "KB_NOT_FOUND"


def test_create_knowledge_base_validation_error(kb_client: TestClient) -> None:
    response = kb_client.post("/v1/knowledge-bases", json={"name": ""})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- Files ---


def test_get_file_detail_happy_path(kb_client: TestClient) -> None:
    kb_id = _create_kb(kb_client, name="file-detail-kb")
    file_id = _index_file(kb_client, kb_id, file_name="guide.txt", content=b"hello file detail")

    response = kb_client.get(f"/v1/knowledge-bases/{kb_id}/files/{file_id}")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["id"] == file_id
    assert data["name"] == "guide.txt"
    assert data["status"] == "available"
    assert data["charCount"] == len(b"hello file detail")


def test_get_unknown_file_returns_file_not_found(kb_client: TestClient) -> None:
    kb_id = _create_kb(kb_client, name="file-missing-kb")

    response = kb_client.get(f"/v1/knowledge-bases/{kb_id}/files/file_missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"
    assert response.json()["error"]["details"]["fileId"] == "file_missing"


def test_delete_file_happy_path(kb_client: TestClient) -> None:
    kb_id = _create_kb(kb_client, name="file-delete-kb")
    file_id = _index_file(kb_client, kb_id, file_name="temp.txt", content=b"delete me")

    delete_response = kb_client.delete(f"/v1/knowledge-bases/{kb_id}/files/{file_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["data"] == {"success": True}

    missing_response = kb_client.get(f"/v1/knowledge-bases/{kb_id}/files/{file_id}")
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "FILE_NOT_FOUND"


def test_batch_delete_reports_missing_files(kb_client: TestClient) -> None:
    kb_id = _create_kb(kb_client, name="batch-delete-kb")
    file_id = _index_file(kb_client, kb_id, file_name="keep.txt", content=b"content")

    response = kb_client.post(
        f"/v1/knowledge-bases/{kb_id}/files:batch-delete",
        json={"fileIds": [file_id, "file_missing"]},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["succeeded"] == [file_id]
    assert len(data["failed"]) == 1
    assert data["failed"][0]["fileId"] == "file_missing"
    assert data["failed"][0]["code"] == "FILE_NOT_FOUND"


# --- HitTest ---


def test_hit_test_happy_path(kb_client: TestClient) -> None:
    kb_id = _create_kb(kb_client, name="hit-kb")
    _index_file(
        kb_client,
        kb_id,
        file_name="notes.txt",
        content=b"Canadian tax-free savings account overview",
    )

    response = kb_client.post(
        f"/v1/knowledge-bases/{kb_id}/hit-test",
        json={"query": "tax-free savings", "topK": 3},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["latencyMs"] >= 0
    assert len(data["results"]) >= 1


def test_hit_test_empty_query_returns_error(kb_client: TestClient) -> None:
    kb_id = _create_kb(kb_client, name="hit-empty-kb")

    response = kb_client.post(
        f"/v1/knowledge-bases/{kb_id}/hit-test",
        json={"query": "   ", "topK": 5},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "HIT_TEST_EMPTY_QUERY"


def test_hit_test_invalid_topk_returns_error(kb_client: TestClient) -> None:
    kb_id = _create_kb(kb_client, name="hit-topk-kb")

    response = kb_client.post(
        f"/v1/knowledge-bases/{kb_id}/hit-test",
        json={"query": "hello", "topK": 0},
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "HIT_TEST_INVALID_TOPK"
    assert error["details"]["min"] == 1


# --- Upload ---


def test_presign_and_complete_upload_happy_path(domain_client: TestClient) -> None:
    kb_id = _create_kb(domain_client)
    headers = _login_headers(domain_client)

    presign_response = domain_client.post(
        "/v1/uploads/presign",
        headers=headers,
        json={
            "knowledgeBaseId": kb_id,
            "files": [
                {
                    "fileName": "report.pdf",
                    "mimeType": "application/pdf",
                    "sizeBytes": 2048,
                }
            ],
        },
    )
    assert presign_response.status_code == 201
    upload_item = presign_response.json()["data"]["uploads"][0]

    complete_response = domain_client.post(
        f"/v1/uploads/{upload_item['uploadId']}:complete",
        headers=headers,
        json={
            "fileId": upload_item["fileId"],
            "storageKey": upload_item["storageKey"],
            "etag": "demo-etag",
        },
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["data"]["status"] == "uploaded"


def test_presign_unknown_kb_returns_kb_not_found(domain_client: TestClient) -> None:
    headers = _login_headers(domain_client)

    response = domain_client.post(
        "/v1/uploads/presign",
        headers=headers,
        json={
            "knowledgeBaseId": "kb_missing",
            "files": [
                {
                    "fileName": "report.pdf",
                    "mimeType": "application/pdf",
                    "sizeBytes": 1024,
                }
            ],
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "KB_NOT_FOUND"


def test_presign_unsupported_file_type(domain_client: TestClient) -> None:
    kb_id = _create_kb(domain_client)
    headers = _login_headers(domain_client)

    response = domain_client.post(
        "/v1/uploads/presign",
        headers=headers,
        json={
            "knowledgeBaseId": kb_id,
            "files": [
                {
                    "fileName": "virus.exe",
                    "mimeType": "application/octet-stream",
                    "sizeBytes": 100,
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FILE_TYPE_UNSUPPORTED"


def test_presign_validation_empty_files(domain_client: TestClient) -> None:
    kb_id = _create_kb(domain_client)
    headers = _login_headers(domain_client)

    response = domain_client.post(
        "/v1/uploads/presign",
        headers=headers,
        json={"knowledgeBaseId": kb_id, "files": []},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- ImportJobs ---


def test_create_and_get_import_job_happy_path(domain_client: TestClient) -> None:
    kb_id = _create_kb(domain_client)
    headers = _login_headers(domain_client)

    create_response = domain_client.post(
        f"/v1/knowledge-bases/{kb_id}/import-jobs",
        headers=headers,
        json={
            "fileIds": ["file_a", "file_b"],
            "chunkStrategy": "default",
            "metadata": {"includeFileName": True, "includeHeadings": False},
        },
    )
    assert create_response.status_code == 201
    created = create_response.json()["data"]
    assert created["knowledgeBaseId"] == kb_id
    assert created["status"] == "queued"
    assert created["stage"] == "upload"
    job_id = created["id"]

    get_response = domain_client.get(f"/v1/import-jobs/{job_id}", headers=headers)
    assert get_response.status_code == 200
    assert get_response.json()["data"]["id"] == job_id


def test_create_import_job_empty_file_ids(domain_client: TestClient) -> None:
    kb_id = _create_kb(domain_client)
    headers = _login_headers(domain_client)

    response = domain_client.post(
        f"/v1/knowledge-bases/{kb_id}/import-jobs",
        headers=headers,
        json={"fileIds": [], "chunkStrategy": "default"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_unknown_import_job_returns_not_found(domain_client: TestClient) -> None:
    headers = _login_headers(domain_client)

    response = domain_client.get("/v1/import-jobs/job_missing", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "IMPORT_JOB_NOT_FOUND"


def test_cancel_import_job_happy_path(domain_client: TestClient) -> None:
    kb_id = _create_kb(domain_client)
    headers = _login_headers(domain_client)

    create_response = domain_client.post(
        f"/v1/knowledge-bases/{kb_id}/import-jobs",
        headers=headers,
        json={"fileIds": ["file_1"], "chunkStrategy": "default"},
    )
    job_id = create_response.json()["data"]["id"]

    cancel_response = domain_client.post(
        f"/v1/import-jobs/{job_id}:cancel",
        headers=headers,
    )
    assert cancel_response.status_code == 200
    assert cancel_response.json()["data"]["status"] == "cancelled"


def test_retry_failed_import_job_happy_path(domain_client: TestClient) -> None:
    kb_id = _create_kb(domain_client)
    headers = _login_headers(domain_client)
    service: ImportJobService = domain_client.app.state.import_job_service
    import_job_repo: ImportJobRepository = service._jobs

    create_response = domain_client.post(
        f"/v1/knowledge-bases/{kb_id}/import-jobs",
        headers=headers,
        json={"fileIds": ["file_1"], "chunkStrategy": "default"},
    )
    job_id = create_response.json()["data"]["id"]
    import_job_repo.update_progress(job_id, status=ImportJobStatus.RUNNING)
    import_job_repo.update_progress(
        job_id,
        status=ImportJobStatus.FAILED,
        stage=ImportJobStage.PARSE,
        error_code="IMPORT_PARSE_FAILED",
        error_message="parse failed",
    )

    retry_response = domain_client.post(
        f"/v1/import-jobs/{job_id}:retry",
        headers=headers,
        json={"options": {"chunkStrategy": "custom"}},
    )
    assert retry_response.status_code == 201
    retried = retry_response.json()["data"]
    assert retried["retryOf"] == job_id
    assert retried["status"] == "queued"
