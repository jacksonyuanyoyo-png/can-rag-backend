from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_openapi_only_exposes_test_api_for_now() -> None:
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/test/ping" in paths
    assert all(not path.startswith("/knowledge_base") for path in paths)
