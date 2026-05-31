from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.import_job_repository import ImportJobRepository
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.upload_repository import UploadRepository
from app.services.auth.auth_service import AuthService
from app.services.auth.refresh_store import InMemoryRefreshStore
from app.services.import_job_service import ImportJobService
from app.services.knowledge_base_adapter import create_kb
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.rag.pipeline import RagPipeline
from app.services.upload_service import UploadService
from app.services.web_import_service import WebImportService
from app.main import app

_FIXTURE_HTML = Path(__file__).parent / "fixtures" / "web" / "article.html"


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
def web_import_client(
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
        DATABASE_URL=database_url,
        RAG_BACKEND="local",
        WEB_ENABLE_BROWSER_FALLBACK=False,
        WEB_MIN_CONTENT_CHARS=80,
    )
    repository = KnowledgeBaseRepository(settings.metadata_path_resolved)
    rag_pipeline = RagPipeline(settings=settings)
    kb_service = KnowledgeBaseService(
        settings=settings,
        repository=repository,
        rag_pipeline=rag_pipeline,
    )

    upload_repo = UploadRepository(database_url)
    kb_service.attach_upload_repository(upload_repo)
    import_job_repo = ImportJobRepository(database_url)
    idempotency_repo = IdempotencyRepository(database_url)
    import_job_service = ImportJobService(
        import_job_repository=import_job_repo,
        idempotency_repository=idempotency_repo,
    )
    web_import_service = WebImportService(
        settings=settings,
        upload_repository=upload_repo,
        knowledge_base_repository=repository,
        import_job_service=import_job_service,
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
        test_client.app.state.upload_service = UploadService(
            settings=settings,
            upload_repository=upload_repo,
            knowledge_base_repository=repository,
        )
        test_client.app.state.import_job_service = import_job_service
        test_client.app.state.web_import_service = web_import_service
        test_client.app.state.idempotency_repository = idempotency_repo
        test_client.app.state.database_status = {"status": "ok"}
        yield test_client


def test_web_import_service_persists_markdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    db_schema_ready: None,
) -> None:
    metadata_path = tmp_path / "knowledge_bases.json"
    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps({"knowledge_bases": {}}), encoding="utf-8")

    settings = Settings(
        LOCAL_METADATA_PATH=str(metadata_path),
        LOCAL_UPLOAD_ROOT=str(upload_root),
        DATABASE_URL=database_url,
        RAG_BACKEND="local",
        WEB_ENABLE_BROWSER_FALLBACK=False,
        WEB_MIN_CONTENT_CHARS=80,
    )
    repository = KnowledgeBaseRepository(settings.metadata_path_resolved)
    kb_service = KnowledgeBaseService(
        settings=settings,
        repository=repository,
        rag_pipeline=RagPipeline(settings=settings),
    )
    kb = create_kb(kb_service, name=f"web-kb-{uuid4().hex[:6]}", description="")

    html = _FIXTURE_HTML.read_text(encoding="utf-8")
    url = "https://www.example.com/articles/resp-grants"

    def fake_fetch(target_url: str, *, settings: Settings, user_agent: str | None = None):
        return html, target_url

    monkeypatch.setattr(
        "app.services.rag.parsing.web_extractor.fetch_html",
        fake_fetch,
    )
    monkeypatch.setattr(
        "app.services.web_import_service.validate_web_url",
        lambda value: value.strip(),
    )

    upload_repo = UploadRepository(database_url)
    service = WebImportService(
        settings=settings,
        upload_repository=upload_repo,
        knowledge_base_repository=repository,
    )
    result = service.import_url(
        knowledge_base_id=kb.id,
        url=url,
        user_id="user_test",
        auto_import=False,
    )

    stored = upload_root / result.storage_key
    assert stored.exists()
    content = stored.read_text(encoding="utf-8")
    assert "can-rag-source" in content
    assert "CESG" in content
    record = upload_repo.get_kb_file(result.file_id)
    assert record is not None
    assert record.file_name.endswith(".md")


def test_web_import_api_route(
    web_import_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = _FIXTURE_HTML.read_text(encoding="utf-8")

    def fake_fetch(target_url: str, *, settings: Settings, user_agent: str | None = None):
        return html, target_url

    monkeypatch.setattr(
        "app.services.rag.parsing.web_extractor.fetch_html",
        fake_fetch,
    )
    monkeypatch.setattr(
        "app.services.web_import_service.validate_web_url",
        lambda value: value.strip(),
    )

    kb_response = web_import_client.post(
        "/v1/knowledge-bases",
        json={"name": f"web-api-{uuid4().hex[:6]}", "description": "demo"},
    )
    assert kb_response.status_code == 201
    kb_id = kb_response.json()["data"]["id"]
    headers = _login_headers(web_import_client)

    response = web_import_client.post(
        f"/v1/knowledge-bases/{kb_id}/web-imports",
        headers=headers,
        json={
            "url": "https://www.example.com/articles/resp-grants",
            "autoImport": False,
            "chunking": {
                "strategy": "default",
                "metadata": {
                    "includeFileName": True,
                    "includeHeadings": True,
                },
            },
        },
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["fileId"].startswith("file_")
    assert data["extractionMethod"] in {"trafilatura", "readability"}
