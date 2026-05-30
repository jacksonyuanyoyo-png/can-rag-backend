from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app


def test_app_starts_without_database_url() -> None:
    settings = Settings(DATABASE_URL="", RAG_BACKEND="postgres_pgvector")

    with patch("app.main.get_settings", return_value=settings):
        with TestClient(app) as client:
            response = client.get("/test/ping")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert client.app.state.database_status["configured"] is False
    assert client.app.state.template_service is None
    assert client.app.state.folder_service is None
    assert client.app.state.upload_service is None
    assert client.app.state.import_job_service is None
    assert client.app.state.model_service is not None
    assert client.app.state.auth_service is not None
    assert client.app.state.knowledge_base_service is not None
    assert client.app.state.settings.RAG_BACKEND == "local"


def test_models_available_without_database_url() -> None:
    settings = Settings(DATABASE_URL="")

    with patch("app.main.get_settings", return_value=settings):
        with TestClient(app) as client:
            response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json()["data"]
