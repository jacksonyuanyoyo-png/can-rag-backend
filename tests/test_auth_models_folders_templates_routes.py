from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.repositories.folder_repository import FolderRepository
from app.repositories.template_repository import TemplateRepository
from app.services.auth.auth_service import AuthService
from app.services.auth.jwt_tokens import create_access_token
from app.services.auth.refresh_store import InMemoryRefreshStore
from app.services.folder_service import FolderService
from app.services.model_service import ModelService
from app.services.template_service import TemplateService


def _patch_bootstrap_without_database(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(
        "app.core.bootstrap.initialize_database",
        lambda _settings: {"configured": False, "status": "disabled"},
    )
    monkeypatch.setattr("app.core.bootstrap.is_database_configured", lambda _settings: False)
    settings = Settings(DATABASE_URL="", RAG_BACKEND="local")
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    return settings


def _login(client: TestClient, *, email: str = "admin@example.com", password: str = "admin123") -> str:
    response = client.post("/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["data"]["accessToken"]


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture
def auth_models_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    settings = _patch_bootstrap_without_database(monkeypatch)
    auth_service = AuthService(settings=settings, refresh_store=InMemoryRefreshStore())
    model_service = ModelService(settings=settings)

    with TestClient(app) as test_client:
        test_client.app.state.settings = settings
        test_client.app.state.auth_service = auth_service
        test_client.app.state.model_service = model_service
        test_client.app.state.database_status = {"status": "disabled"}
        yield test_client


@pytest.fixture
def folders_templates_client(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    db_connection,
) -> Iterator[TestClient]:
    settings = _patch_bootstrap_without_database(monkeypatch)
    auth_service = AuthService(settings=settings, refresh_store=InMemoryRefreshStore())

    folder_repo = FolderRepository(database_url, connection=db_connection)
    folder_repo.ensure_schema()
    folder_service = FolderService(folder_repo)

    template_repo = TemplateRepository(database_url, connection=db_connection)
    template_repo.ensure_schema()
    template_service = TemplateService(template_repo)

    with TestClient(app) as test_client:
        test_client.app.state.settings = settings
        test_client.app.state.auth_service = auth_service
        test_client.app.state.folder_service = folder_service
        test_client.app.state.template_service = template_service
        test_client.app.state.database_status = {"status": "ok"}
        yield test_client


# --- Auth ---


def test_auth_login_me_refresh_logout_happy_path(auth_models_client: TestClient) -> None:
    login_response = auth_models_client.post(
        "/v1/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
    )
    assert login_response.status_code == 200
    login_payload = login_response.json()
    assert login_payload["data"]["accessToken"]
    assert login_payload["data"]["expiresIn"] == 1800
    assert login_payload["data"]["user"]["email"] == "admin@example.com"
    assert "requestId" in login_payload
    assert login_response.cookies.get("refresh_token")

    access_token = login_payload["data"]["accessToken"]

    me_response = auth_models_client.get("/v1/auth/me", headers=_auth_headers(access_token))
    assert me_response.status_code == 200
    assert me_response.json()["data"]["id"] == "user_admin"
    assert me_response.json()["data"]["teamId"] == "team_default"

    refresh_response = auth_models_client.post("/v1/auth/refresh")
    assert refresh_response.status_code == 200
    assert refresh_response.json()["data"]["accessToken"]

    logout_response = auth_models_client.post("/v1/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json()["data"]["success"] is True

    expired_refresh = auth_models_client.post("/v1/auth/refresh")
    assert expired_refresh.status_code == 401
    assert expired_refresh.json()["error"]["code"] == "AUTH_REFRESH_EXPIRED"


def test_auth_login_invalid_credentials(auth_models_client: TestClient) -> None:
    response = auth_models_client.post(
        "/v1/auth/login",
        json={"email": "admin@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_auth_login_invalid_request(auth_models_client: TestClient) -> None:
    response = auth_models_client.post(
        "/v1/auth/login",
        json={"email": "   ", "password": "admin123"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "AUTH_INVALID_REQUEST"


def test_auth_me_missing_token(auth_models_client: TestClient) -> None:
    response = auth_models_client.get("/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_MISSING"


def test_auth_me_invalid_token(auth_models_client: TestClient) -> None:
    response = auth_models_client.get("/v1/auth/me", headers=_auth_headers("not-a-valid-jwt"))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_INVALID"


def test_auth_me_expired_token(auth_models_client: TestClient) -> None:
    settings = auth_models_client.app.state.settings
    expired_token = create_access_token(
        user_id="user_admin",
        secret=settings.AUTH_JWT_SECRET,
        expires_in_seconds=-3600,
    )

    response = auth_models_client.get("/v1/auth/me", headers=_auth_headers(expired_token))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_EXPIRED"


# --- Models ---


def test_list_models_happy_path(auth_models_client: TestClient) -> None:
    response = auth_models_client.get("/v1/models")

    assert response.status_code == 200
    payload = response.json()
    assert "requestId" in payload
    models = payload["data"]
    ids = {model["id"] for model in models}
    assert "gpt-4o-mini" in ids
    assert "gpt-5" in ids
    assert "claude-sonnet-4" not in ids
    assert "gemini" not in ids

    gpt5 = next(model for model in models if model["id"] == "gpt-5")
    assert gpt5["name"] == "GPT-5"
    assert gpt5["provider"] == "openai"
    assert gpt5["icon"] == "/models/openai.svg"
    assert gpt5["status"] == "active"
    assert gpt5["visibility"] == "system"


def test_list_models_does_not_require_auth(auth_models_client: TestClient) -> None:
    response = auth_models_client.get("/v1/models")

    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)


def test_list_embedding_models_happy_path(auth_models_client: TestClient) -> None:
    response = auth_models_client.get("/v1/embedding-models")

    assert response.status_code == 200
    payload = response.json()
    assert "requestId" in payload
    models = payload["data"]
    ids = {model["id"] for model in models}
    assert "text-embedding-3-small" in ids
    assert "text-embedding-3-large" in ids
    assert "gpt-4o" not in ids

    small = next(model for model in models if model["id"] == "text-embedding-3-small")
    assert small["dimensions"] == 1536
    assert small["maxInputTokens"] == 8191
    assert small["provider"] == "openai"


# --- Folders ---


def test_folder_crud_happy_path(folders_templates_client: TestClient) -> None:
    token = _login(folders_templates_client)
    headers = _auth_headers(token)
    folder_name = f"Work-{uuid4().hex[:8]}"

    create_response = folders_templates_client.post(
        "/v1/folders",
        json={"name": folder_name},
        headers=headers,
    )
    assert create_response.status_code == 201
    created = create_response.json()["data"]
    assert created["name"] == folder_name
    assert created["id"]
    folder_id = created["id"]

    list_response = folders_templates_client.get("/v1/folders", headers=headers)
    assert list_response.status_code == 200
    folder_ids = [item["id"] for item in list_response.json()["data"]]
    assert folder_id in folder_ids

    renamed = f"Renamed-{uuid4().hex[:8]}"
    update_response = folders_templates_client.patch(
        f"/v1/folders/{folder_id}",
        json={"name": renamed},
        headers=headers,
    )
    assert update_response.status_code == 200
    updated = update_response.json()["data"]
    assert updated["name"] == renamed
    assert updated["updatedAt"]

    delete_response = folders_templates_client.delete(f"/v1/folders/{folder_id}", headers=headers)
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["success"] is True


def test_create_duplicate_folder_returns_conflict(folders_templates_client: TestClient) -> None:
    token = _login(folders_templates_client)
    headers = _auth_headers(token)
    folder_name = f"Duplicate-{uuid4().hex[:8]}"

    assert (
        folders_templates_client.post("/v1/folders", json={"name": folder_name}, headers=headers).status_code
        == 201
    )

    duplicate_response = folders_templates_client.post(
        "/v1/folders",
        json={"name": folder_name},
        headers=headers,
    )

    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["error"]["code"] == "FOLDER_NAME_DUPLICATED"


def test_folder_not_found_returns_error(folders_templates_client: TestClient) -> None:
    token = _login(folders_templates_client)
    headers = _auth_headers(token)
    missing_id = f"fld_{uuid4().hex[:12]}"

    update_response = folders_templates_client.patch(
        f"/v1/folders/{missing_id}",
        json={"name": "Ghost Folder"},
        headers=headers,
    )
    assert update_response.status_code == 404
    assert update_response.json()["error"]["code"] == "FOLDER_NOT_FOUND"

    delete_response = folders_templates_client.delete(f"/v1/folders/{missing_id}", headers=headers)
    assert delete_response.status_code == 404
    assert delete_response.json()["error"]["code"] == "FOLDER_NOT_FOUND"


def test_folders_require_auth(folders_templates_client: TestClient) -> None:
    response = folders_templates_client.get("/v1/folders")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_MISSING"


# --- Templates ---


def test_template_crud_happy_path(folders_templates_client: TestClient) -> None:
    token = _login(folders_templates_client)
    headers = _auth_headers(token)
    template_name = f"Bug Report-{uuid4().hex[:8]}"

    create_response = folders_templates_client.post(
        "/v1/templates",
        json={
            "name": template_name,
            "content": "**Description:**\nIssue details",
            "snippet": "**Description:**",
        },
        headers=headers,
    )
    assert create_response.status_code == 201
    created = create_response.json()["data"]
    assert created["name"] == template_name
    assert created["content"] == "**Description:**\nIssue details"
    assert created["snippet"] == "**Description:**"
    assert created["createdAt"]
    template_id = created["id"]

    list_response = folders_templates_client.get("/v1/templates", headers=headers)
    assert list_response.status_code == 200
    template_ids = [item["id"] for item in list_response.json()["data"]]
    assert template_id in template_ids

    updated_name = f"{template_name} v2"
    update_response = folders_templates_client.patch(
        f"/v1/templates/{template_id}",
        json={"name": updated_name, "content": "Updated content"},
        headers=headers,
    )
    assert update_response.status_code == 200
    updated = update_response.json()["data"]
    assert updated["name"] == updated_name
    assert updated["content"] == "Updated content"

    delete_response = folders_templates_client.delete(f"/v1/templates/{template_id}", headers=headers)
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["success"] is True


def test_create_duplicate_template_returns_conflict(folders_templates_client: TestClient) -> None:
    token = _login(folders_templates_client)
    headers = _auth_headers(token)
    template_name = f"Daily Standup-{uuid4().hex[:8]}"
    payload = {
        "name": template_name,
        "content": "What did you do yesterday?",
    }

    assert folders_templates_client.post("/v1/templates", json=payload, headers=headers).status_code == 201

    duplicate_response = folders_templates_client.post("/v1/templates", json=payload, headers=headers)

    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["error"]["code"] == "TEMPLATE_NAME_DUPLICATED"


def test_template_not_found_returns_error(folders_templates_client: TestClient) -> None:
    token = _login(folders_templates_client)
    headers = _auth_headers(token)
    missing_id = f"tmpl_{uuid4().hex[:12]}"

    update_response = folders_templates_client.patch(
        f"/v1/templates/{missing_id}",
        json={"name": "Missing Template"},
        headers=headers,
    )
    assert update_response.status_code == 404
    assert update_response.json()["error"]["code"] == "TEMPLATE_NOT_FOUND"

    delete_response = folders_templates_client.delete(f"/v1/templates/{missing_id}", headers=headers)
    assert delete_response.status_code == 404
    assert delete_response.json()["error"]["code"] == "TEMPLATE_NOT_FOUND"


def test_templates_require_auth(folders_templates_client: TestClient) -> None:
    response = folders_templates_client.get("/v1/templates")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_MISSING"
