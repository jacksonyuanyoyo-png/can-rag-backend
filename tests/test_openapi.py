from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

# docs/backend-api-dev-doc.md L1280-L1319 checklist (40 endpoints).
# OpenAPI path params follow FastAPI snake_case conventions.
DOCUMENTED_V1_ENDPOINTS: list[tuple[str, str]] = [
    ("post", "/v1/auth/login"),
    ("post", "/v1/auth/refresh"),
    ("get", "/v1/auth/me"),
    ("post", "/v1/auth/logout"),
    ("get", "/v1/models"),
    ("get", "/v1/embedding-models"),
    ("get", "/v1/conversations"),
    ("post", "/v1/conversations"),
    ("get", "/v1/conversations/{conversation_id}"),
    ("patch", "/v1/conversations/{conversation_id}"),
    ("delete", "/v1/conversations/{conversation_id}"),
    ("get", "/v1/conversations/{conversation_id}/messages"),
    ("post", "/v1/conversations/{conversation_id}/messages"),
    ("post", "/v1/conversations/{conversation_id}/messages:stream"),
    ("post", "/v1/conversations/{conversation_id}/messages/{message_id}:cancel"),
    ("post", "/v1/messages/{message_id}/feedback"),
    ("get", "/v1/folders"),
    ("post", "/v1/folders"),
    ("patch", "/v1/folders/{folder_id}"),
    ("delete", "/v1/folders/{folder_id}"),
    ("get", "/v1/templates"),
    ("post", "/v1/templates"),
    ("patch", "/v1/templates/{template_id}"),
    ("delete", "/v1/templates/{template_id}"),
    ("get", "/v1/knowledge-bases"),
    ("post", "/v1/knowledge-bases"),
    ("get", "/v1/knowledge-bases/{kb_id}"),
    ("patch", "/v1/knowledge-bases/{kb_id}"),
    ("delete", "/v1/knowledge-bases/{kb_id}"),
    ("get", "/v1/knowledge-bases/{kb_id}/files"),
    ("get", "/v1/knowledge-bases/{kb_id}/files/{file_id}"),
    ("get", "/v1/knowledge-bases/{kb_id}/files/{file_id}/raw"),
    ("delete", "/v1/knowledge-bases/{kb_id}/files/{file_id}"),
    ("post", "/v1/knowledge-bases/{kb_id}/files:batch-delete"),
    ("post", "/v1/knowledge-bases/{kb_id}/hit-test"),
    ("get", "/v1/knowledge-bases/{kb_id}/index-stats"),
    ("post", "/v1/uploads/presign"),
    ("post", "/v1/uploads/{upload_id}:complete"),
    ("post", "/v1/knowledge-bases/{kb_id}/import-jobs"),
    ("get", "/v1/import-jobs/{job_id}"),
    ("post", "/v1/import-jobs/{job_id}:cancel"),
    ("post", "/v1/import-jobs/{job_id}:retry"),
]


def test_openapi_includes_all_documented_v1_endpoints() -> None:
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert len(DOCUMENTED_V1_ENDPOINTS) == 42

    missing: list[str] = []
    for method, path in DOCUMENTED_V1_ENDPOINTS:
        if path not in paths:
            missing.append(f"{method.upper()} {path} (path missing)")
        elif method not in paths[path]:
            missing.append(f"{method.upper()} {path} (method missing)")

    assert not missing, "Missing documented endpoints in OpenAPI:\n" + "\n".join(missing)


def test_openapi_documented_paths_use_v1_prefix() -> None:
    client = TestClient(app)
    paths = client.get("/openapi.json").json()["paths"]

    for _, path in DOCUMENTED_V1_ENDPOINTS:
        assert path.startswith("/v1/"), f"Expected /v1 prefix for documented path: {path}"


def test_openapi_has_no_legacy_knowledge_base_paths() -> None:
    client = TestClient(app)
    paths = client.get("/openapi.json").json()["paths"]

    assert all(not path.startswith("/knowledge_base") for path in paths)
