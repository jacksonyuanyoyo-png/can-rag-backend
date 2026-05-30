from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.services.auth.auth_service import AuthService
from app.services.auth.refresh_store import InMemoryRefreshStore


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(
        "app.core.bootstrap.initialize_database",
        lambda _settings: {"configured": False, "status": "disabled"},
    )
    monkeypatch.setattr("app.core.bootstrap.is_database_configured", lambda _settings: False)
    settings = Settings(DATABASE_URL="", RAG_BACKEND="local")
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    auth_service = AuthService(settings=settings, refresh_store=InMemoryRefreshStore())

    with TestClient(app) as client:
        client.app.state.settings = settings
        client.app.state.auth_service = auth_service
        client.app.state.database_status = {"status": "disabled"}
        yield client


def test_auth_login_me_refresh_logout_flow(auth_client: TestClient) -> None:
    client = auth_client

    login_response = client.post(
        "/v1/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
    )
    assert login_response.status_code == 200
    login_payload = login_response.json()
    assert "data" in login_payload
    assert "requestId" in login_payload
    assert login_payload["data"]["accessToken"]
    assert login_payload["data"]["expiresIn"] == 1800
    assert login_payload["data"]["user"]["email"] == "admin@example.com"
    assert "kb:read" in login_payload["data"]["user"]["permissions"]
    assert login_response.cookies.get("refresh_token")

    access_token = login_payload["data"]["accessToken"]

    me_response = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_response.status_code == 200
    me_payload = me_response.json()
    assert me_payload["data"]["id"] == "user_admin"
    assert me_payload["data"]["teamId"] == "team_default"

    refresh_response = client.post("/v1/auth/refresh")
    assert refresh_response.status_code == 200
    refresh_payload = refresh_response.json()
    assert refresh_payload["data"]["accessToken"]
    assert refresh_response.cookies.get("refresh_token")

    logout_response = client.post("/v1/auth/logout")
    assert logout_response.status_code == 200
    assert logout_response.json()["data"]["success"] is True

    expired_refresh = client.post("/v1/auth/refresh")
    assert expired_refresh.status_code == 401
    assert expired_refresh.json()["error"]["code"] == "AUTH_REFRESH_EXPIRED"


def test_auth_login_invalid_credentials(auth_client: TestClient) -> None:
    client = auth_client

    response = client.post(
        "/v1/auth/login",
        json={"email": "admin@example.com", "password": "wrong"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_auth_me_missing_token(auth_client: TestClient) -> None:
    client = auth_client

    response = client.get("/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_MISSING"
