from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_ping_returns_runtime_info() -> None:
    client = TestClient(app)

    response = client.get("/test/ping")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "Fidelity RAG Gateway"
    assert payload["python"].startswith("3.13")
    assert payload["routes"] >= 1
