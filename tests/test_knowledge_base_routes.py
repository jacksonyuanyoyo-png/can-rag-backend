from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app
from app.repositories.kb_data_index_repository import KbDataIndexRepository
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.services.knowledge_base_service import KnowledgeBaseService
from app.services.rag.embedder import HashEmbeddingService
from app.services.rag.pipeline import RagPipeline


def _test_embedding() -> list[float]:
    dims = get_settings().RAG_EMBEDDING_DIMENSIONS
    vec = [0.0] * dims
    vec[0] = 0.1
    vec[1] = 0.2
    return vec


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    metadata_path = tmp_path / "knowledge_bases.json"
    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps({"knowledge_bases": {}}), encoding="utf-8")

    settings = Settings(
        LOCAL_METADATA_PATH=str(metadata_path),
        LOCAL_UPLOAD_ROOT=str(upload_root),
    )
    repository = KnowledgeBaseRepository(settings.metadata_path_resolved)
    rag_pipeline = RagPipeline(settings=settings)
    kb_service = KnowledgeBaseService(
        settings=settings,
        repository=repository,
        rag_pipeline=rag_pipeline,
    )

    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)

    with TestClient(app) as test_client:
        test_client.app.state.settings = settings
        test_client.app.state.knowledge_base_service = kb_service
        test_client.app.state.database_status = {"status": "skipped"}
        yield test_client


def test_list_knowledge_bases_empty(client: TestClient) -> None:
    response = client.get("/v1/knowledge-bases")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == []
    assert payload["pagination"] == {
        "page": 1,
        "pageSize": 20,
        "total": 0,
        "hasMore": False,
    }
    assert payload["requestId"]


def test_create_knowledge_base_rejects_invalid_embedding_model(client: TestClient) -> None:
    response = client.post(
        "/v1/knowledge-bases",
        json={
            "name": "bad-embedding-kb",
            "description": "demo",
            "embeddingModelId": "gpt-4o",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_get_and_delete_knowledge_base(client: TestClient) -> None:
    create_response = client.post(
        "/v1/knowledge-bases",
        json={
            "name": "demo-kb",
            "description": "demo description",
            "embeddingModelId": "text-embedding-3-small",
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()["data"]
    assert created["name"] == "demo-kb"
    assert created["description"] == "demo description"
    assert created["embeddingModelId"] == "text-embedding-3-small"
    assert created["fileCount"] == 0
    assert created["resourceType"] == "personal"
    assert created["updatedAt"]
    kb_id = created["id"]

    get_response = client.get(f"/v1/knowledge-bases/{kb_id}")
    assert get_response.status_code == 200
    assert get_response.json()["data"]["id"] == kb_id

    list_response = client.get("/v1/knowledge-bases")
    assert list_response.status_code == 200
    assert list_response.json()["pagination"]["total"] == 1

    delete_response = client.delete(f"/v1/knowledge-bases/{kb_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["data"] == {"success": True}

    missing_response = client.get(f"/v1/knowledge-bases/{kb_id}")
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "KB_NOT_FOUND"


def test_create_duplicate_name_returns_conflict(client: TestClient) -> None:
    payload = {"name": "dup-kb", "description": "first"}
    assert client.post("/v1/knowledge-bases", json=payload).status_code == 201

    duplicate_response = client.post("/v1/knowledge-bases", json=payload)

    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["error"]["code"] == "KB_NAME_DUPLICATED"


def test_list_supports_search_and_scope_filter(client: TestClient) -> None:
    service: KnowledgeBaseService = client.app.state.knowledge_base_service
    team_kb = service.create_kb(name="team-kb", description="shared docs")
    team_kb.backend_refs["resource_type"] = "team"
    service.save_kb(team_kb)

    personal_kb = service.create_kb(name="personal-kb", description="private docs")
    personal_kb.backend_refs["resource_type"] = "personal"
    service.save_kb(personal_kb)

    scoped_response = client.get("/v1/knowledge-bases", params={"scope": "team"})
    assert scoped_response.status_code == 200
    scoped_items = scoped_response.json()["data"]
    assert len(scoped_items) == 1
    assert scoped_items[0]["resourceType"] == "team"

    search_response = client.get("/v1/knowledge-bases", params={"q": "private"})
    assert search_response.status_code == 200
    assert search_response.json()["pagination"]["total"] == 1
    assert search_response.json()["data"][0]["name"] == "personal-kb"


def test_list_files_and_index_stats(client: TestClient) -> None:
    create_response = client.post(
        "/v1/knowledge-bases",
        json={"name": "files-kb", "description": "with docs"},
    )
    assert create_response.status_code == 201
    kb_id = create_response.json()["data"]["id"]

    service: KnowledgeBaseService = client.app.state.knowledge_base_service
    metadata = service.get_kb("files-kb")
    assert metadata is not None
    service.index_document(
        knowledge_base=metadata.name,
        file_name="guide.txt",
        content=b"hello knowledge base",
        content_type="text/plain",
    )

    files_response = client.get(f"/v1/knowledge-bases/{kb_id}/files")
    assert files_response.status_code == 200
    files_payload = files_response.json()
    assert files_payload["pagination"]["total"] == 1
    file_item = files_payload["data"][0]
    assert file_item["name"] == "guide.txt"
    assert file_item["format"] == "txt"
    assert file_item["status"] == "available"
    assert file_item["charCount"] == len("hello knowledge base")
    assert file_item["uploadedAt"]
    assert file_item["tags"] is None

    stats_response = client.get(f"/v1/knowledge-bases/{kb_id}/index-stats")
    assert stats_response.status_code == 200
    stats = stats_response.json()["data"]
    assert stats["status"] == "ready"
    assert stats["fileCount"] == 1
    assert stats["chunkCount"] >= 1
    assert stats["indexedChunkCount"] == stats["chunkCount"]
    assert stats["failedFileCount"] == 0
    assert stats["lastIndexedAt"]

    missing_response = client.get(f"/v1/knowledge-bases/kb-missing/files")
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "KB_NOT_FOUND"


def test_hit_test_returns_results_and_latency(client: TestClient) -> None:
    create_response = client.post(
        "/v1/knowledge-bases",
        json={"name": "hit-test-kb", "description": "for hit test"},
    )
    assert create_response.status_code == 201
    kb_id = create_response.json()["data"]["id"]

    service: KnowledgeBaseService = client.app.state.knowledge_base_service
    service.index_document(
        knowledge_base="hit-test-kb",
        file_name="notes.txt",
        content=b"This document explains tax-free savings accounts in Canada.",
    )

    response = client.post(
        f"/v1/knowledge-bases/{kb_id}/hit-test",
        json={"query": "tax-free savings", "topK": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requestId"]
    data = payload["data"]
    assert isinstance(data["latencyMs"], int)
    assert data["latencyMs"] >= 0
    assert len(data["results"]) >= 1
    first = data["results"][0]
    assert {"fileId", "chunkId", "score", "snippet"}.issubset(first.keys())
    assert "tax-free" in first["snippet"].lower() or "savings" in first["snippet"].lower()


def test_hit_test_multi_vector_returns_data_page_and_dedupes_indexes(
    client: TestClient,
    database_url: str,
) -> None:
    old_settings = client.app.state.settings
    settings = Settings(
        LOCAL_METADATA_PATH=str(old_settings.LOCAL_METADATA_PATH),
        LOCAL_UPLOAD_ROOT=str(old_settings.LOCAL_UPLOAD_ROOT),
        DATABASE_URL=database_url,
        RAG_BACKEND="postgres_pgvector",
        RAG_EMBEDDING_DIMENSIONS=old_settings.RAG_EMBEDDING_DIMENSIONS,
    )
    service: KnowledgeBaseService = client.app.state.knowledge_base_service
    repository = service._repository
    rag_pipeline = RagPipeline(settings=settings)
    kb_service = KnowledgeBaseService(
        settings=settings,
        repository=repository,
        rag_pipeline=rag_pipeline,
    )
    client.app.state.settings = settings
    client.app.state.knowledge_base_service = kb_service

    kb_data_index_repo = KbDataIndexRepository(database_url)
    kb_data_index_repo.ensure_schema()

    create_response = client.post(
        "/v1/knowledge-bases",
        json={"name": f"mv-hit-{uuid4().hex[:8]}", "description": "multi-vector hit test"},
    )
    assert create_response.status_code == 201
    kb_id = create_response.json()["data"]["id"]
    file_id = f"file_mv_{uuid4().hex[:8]}"

    kb_data_index_repo.ensure_kb_file_stub(kb_id=kb_id, file_id=file_id, file_name="hit.txt")

    embedder = HashEmbeddingService(dimensions=settings.RAG_EMBEDDING_DIMENSIONS)
    query = "multi vector retrieval topic"
    text_primary = "multi vector retrieval topic primary chunk"
    text_secondary = "unrelated secondary chunk content"
    emb_strong = embedder.embed(f"{text_primary} strong index")
    emb_weak = embedder.embed(f"{text_primary} weak index")
    emb_secondary = embedder.embed(text_secondary)

    data_primary = "data_mv_primary"
    data_secondary_id = "data_mv_secondary"
    kb_data_index_repo.upsert_data(
        kb_id=kb_id,
        file_id=file_id,
        data_id=data_primary,
        text=text_primary,
        page=3,
        chunk_index=0,
        citation={"file_name": "hit.txt", "page": 3, "data_id": data_primary},
    )
    kb_data_index_repo.upsert_index(
        kb_id=kb_id,
        file_id=file_id,
        data_id=data_primary,
        index_id=f"{data_primary}-strong",
        text=f"{text_primary} strong index",
        embedding=emb_strong,
    )
    kb_data_index_repo.upsert_index(
        kb_id=kb_id,
        file_id=file_id,
        data_id=data_primary,
        index_id=f"{data_primary}-weak",
        text=f"{text_primary} weak index",
        embedding=emb_weak,
    )
    kb_data_index_repo.upsert_data(
        kb_id=kb_id,
        file_id=file_id,
        data_id=data_secondary_id,
        text=text_secondary,
        page=9,
        chunk_index=1,
        citation={"file_name": "hit.txt", "page": 9, "data_id": data_secondary_id},
    )
    kb_data_index_repo.upsert_index(
        kb_id=kb_id,
        file_id=file_id,
        data_id=data_secondary_id,
        index_id=f"{data_secondary_id}-000",
        text=text_secondary,
        embedding=emb_secondary,
    )

    response = client.post(
        f"/v1/knowledge-bases/{kb_id}/hit-test",
        json={"query": query, "topK": 5},
    )

    assert response.status_code == 200
    results = response.json()["data"]["results"]
    primary_hits = [item for item in results if item["chunkId"] == data_primary]
    assert len(primary_hits) == 1
    primary = primary_hits[0]
    assert primary["fileId"] == file_id
    assert primary["page"] == 3
    assert text_primary in primary["snippet"]
    assert isinstance(primary["score"], float)

    secondary_hits = [item for item in results if item["chunkId"] == data_secondary_id]
    assert len(secondary_hits) <= 1
    if secondary_hits:
        assert secondary_hits[0]["page"] == 9
        assert secondary_hits[0]["fileId"] == file_id


def test_hit_test_empty_query_returns_business_error(client: TestClient) -> None:
    create_response = client.post(
        "/v1/knowledge-bases",
        json={"name": "hit-test-empty", "description": ""},
    )
    kb_id = create_response.json()["data"]["id"]

    response = client.post(
        f"/v1/knowledge-bases/{kb_id}/hit-test",
        json={"query": "   ", "topK": 5},
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "HIT_TEST_EMPTY_QUERY"
    assert response.json()["requestId"]


def test_hit_test_invalid_topk_returns_business_error(client: TestClient) -> None:
    create_response = client.post(
        "/v1/knowledge-bases",
        json={"name": "hit-test-topk", "description": ""},
    )
    kb_id = create_response.json()["data"]["id"]

    response = client.post(
        f"/v1/knowledge-bases/{kb_id}/hit-test",
        json={"query": "hello", "topK": 0},
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "HIT_TEST_INVALID_TOPK"
    assert error["details"]["min"] == 1
    assert error["details"]["max"] == 50


def test_hit_test_unknown_kb_returns_not_found(client: TestClient) -> None:
    response = client.post(
        "/v1/knowledge-bases/kb_missing/hit-test",
        json={"query": "hello", "topK": 5},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "KB_NOT_FOUND"


def test_update_knowledge_base(client: TestClient) -> None:
    create_response = client.post(
        "/v1/knowledge-bases",
        json={"name": "patch-kb", "description": "before"},
    )
    assert create_response.status_code == 201
    kb_id = create_response.json()["data"]["id"]

    patch_response = client.patch(
        f"/v1/knowledge-bases/{kb_id}",
        json={"description": "after", "resourceType": "team"},
    )
    assert patch_response.status_code == 200
    updated = patch_response.json()["data"]
    assert updated["description"] == "after"
    assert updated["resourceType"] == "team"
    assert updated["id"] == kb_id


def test_update_knowledge_base_duplicate_name_returns_conflict(client: TestClient) -> None:
    assert client.post("/v1/knowledge-bases", json={"name": "kb-a", "description": ""}).status_code == 201
    kb_b = client.post("/v1/knowledge-bases", json={"name": "kb-b", "description": ""}).json()["data"]["id"]

    response = client.patch(
        f"/v1/knowledge-bases/{kb_b}",
        json={"name": "kb-a"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "KB_NAME_DUPLICATED"


def test_get_delete_and_batch_delete_files(client: TestClient) -> None:
    create_response = client.post(
        "/v1/knowledge-bases",
        json={"name": "file-ops-kb", "description": ""},
    )
    kb_id = create_response.json()["data"]["id"]

    service: KnowledgeBaseService = client.app.state.knowledge_base_service
    service.index_document(
        knowledge_base="file-ops-kb",
        file_name="alpha.txt",
        content=b"alpha content",
        content_type="text/plain",
    )
    metadata = service.get_kb("file-ops-kb")
    assert metadata is not None
    file_id = next(iter(metadata.documents.keys()))

    detail_response = client.get(f"/v1/knowledge-bases/{kb_id}/files/{file_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["data"]
    assert detail["id"] == file_id
    assert detail["name"] == "alpha.txt"
    assert detail["mimeType"] == "text/plain"
    assert detail["sizeBytes"] == len(b"alpha content")
    assert detail["errorMessage"] is None

    service.index_document(
        knowledge_base="file-ops-kb",
        file_name="beta.txt",
        content=b"beta content",
    )
    metadata = service.get_kb("file-ops-kb")
    assert metadata is not None
    file_ids = list(metadata.documents.keys())
    assert len(file_ids) == 2

    delete_response = client.delete(f"/v1/knowledge-bases/{kb_id}/files/{file_ids[0]}")
    assert delete_response.status_code == 200
    assert delete_response.json()["data"] == {"success": True}

    batch_response = client.post(
        f"/v1/knowledge-bases/{kb_id}/files:batch-delete",
        json={"fileIds": [file_ids[1], "file_missing"]},
    )
    assert batch_response.status_code == 200
    batch_data = batch_response.json()["data"]
    assert batch_data["succeeded"] == [file_ids[1]]
    assert len(batch_data["failed"]) == 1
    assert batch_data["failed"][0]["fileId"] == "file_missing"
    assert batch_data["failed"][0]["code"] == "FILE_NOT_FOUND"

    missing_response = client.get(f"/v1/knowledge-bases/{kb_id}/files/file_missing")
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "FILE_NOT_FOUND"


def test_delete_file_in_use_returns_conflict(client: TestClient) -> None:
    create_response = client.post(
        "/v1/knowledge-bases",
        json={"name": "in-use-kb", "description": ""},
    )
    kb_id = create_response.json()["data"]["id"]

    service: KnowledgeBaseService = client.app.state.knowledge_base_service
    service.index_document(
        knowledge_base="in-use-kb",
        file_name="locked.txt",
        content=b"locked",
    )
    metadata = service.get_kb("in-use-kb")
    assert metadata is not None
    file_id = next(iter(metadata.documents.keys()))
    document = metadata.documents[file_id]
    document.backend_refs["in_use"] = True
    service.save_kb(metadata)

    response = client.delete(f"/v1/knowledge-bases/{kb_id}/files/{file_id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FILE_IN_USE"


def test_list_file_chunks_pagination_filter_and_indexes(
    client: TestClient,
    database_url: str,
    db_connection,
) -> None:
    kb_data_index_repo = KbDataIndexRepository(database_url, connection=db_connection)
    kb_data_index_repo.ensure_schema()

    create_response = client.post(
        "/v1/knowledge-bases",
        json={"name": f"chunks-kb-{uuid4().hex[:8]}", "description": "chunks"},
    )
    assert create_response.status_code == 201
    kb_id = create_response.json()["data"]["id"]
    file_id = f"file_chunks_{uuid4().hex[:8]}"
    kb_data_index_repo.ensure_kb_file_stub(kb_id=kb_id, file_id=file_id)

    embedding = _test_embedding()
    for index, (data_id, text) in enumerate(
        [
            ("data_alpha", "alpha chunk text"),
            ("data_beta", "beta chunk text"),
            ("data_gamma", "gamma chunk text"),
        ]
    ):
        kb_data_index_repo.upsert_data(
            kb_id=kb_id,
            file_id=file_id,
            data_id=data_id,
            text=text,
            page=index + 1,
            chunk_index=index,
            citation={"source": "test", "ordinal": index},
        )
        kb_data_index_repo.upsert_index(
            kb_id=kb_id,
            file_id=file_id,
            data_id=data_id,
            index_id=f"idx_{data_id}",
            text=f"index for {data_id}",
            embedding=embedding,
        )

    service: KnowledgeBaseService = client.app.state.knowledge_base_service
    service._kb_data_index_repo = kb_data_index_repo

    page_one = client.get(
        f"/v1/knowledge-bases/{kb_id}/files/{file_id}/chunks",
        params={"page": 1, "pageSize": 2},
    )
    assert page_one.status_code == 200
    payload = page_one.json()
    assert payload["pagination"] == {
        "page": 1,
        "pageSize": 2,
        "total": 3,
        "hasMore": True,
    }
    assert len(payload["data"]) == 2
    first = payload["data"][0]
    assert first["dataId"] == "data_alpha"
    assert first["text"] == "alpha chunk text"
    assert first["charCount"] == len("alpha chunk text")
    assert first["page"] == 1
    assert first["chunkIndex"] == 0
    assert first["citation"] == {"source": "test", "ordinal": 0}
    assert len(first["indexes"]) == 1
    assert first["indexes"][0] == {
        "indexId": "idx_data_alpha",
        "text": "index for data_alpha",
    }
    assert "embedding" not in first["indexes"][0]

    filtered = client.get(
        f"/v1/knowledge-bases/{kb_id}/files/{file_id}/chunks",
        params={"q": "beta"},
    )
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["pagination"]["total"] == 1
    assert filtered_payload["data"][0]["dataId"] == "data_beta"

    missing_kb = client.get(f"/v1/knowledge-bases/kb-missing/files/{file_id}/chunks")
    assert missing_kb.status_code == 404
    assert missing_kb.json()["error"]["code"] == "KB_NOT_FOUND"


def test_get_file_chunk_with_context_target_and_neighbors(
    client: TestClient,
    database_url: str,
    db_connection,
) -> None:
    kb_data_index_repo = KbDataIndexRepository(database_url, connection=db_connection)
    kb_data_index_repo.ensure_schema()

    create_response = client.post(
        "/v1/knowledge-bases",
        json={"name": f"chunk-ctx-kb-{uuid4().hex[:8]}", "description": "chunk context"},
    )
    assert create_response.status_code == 201
    kb_id = create_response.json()["data"]["id"]
    file_id = f"file_ctx_{uuid4().hex[:8]}"
    kb_data_index_repo.ensure_kb_file_stub(kb_id=kb_id, file_id=file_id)

    embedding = _test_embedding()
    chunk_texts = [f"chunk text {index}" for index in range(5)]
    for index, text in enumerate(chunk_texts):
        data_id = f"data_{index}"
        kb_data_index_repo.upsert_data(
            kb_id=kb_id,
            file_id=file_id,
            data_id=data_id,
            text=text,
            page=index + 1,
            chunk_index=index,
            citation={"source": "ctx-test", "ordinal": index},
        )
        kb_data_index_repo.upsert_index(
            kb_id=kb_id,
            file_id=file_id,
            data_id=data_id,
            index_id=f"idx_{data_id}",
            text=f"index for {data_id}",
            embedding=embedding,
        )

    service: KnowledgeBaseService = client.app.state.knowledge_base_service
    service._kb_data_index_repo = kb_data_index_repo

    response = client.get(
        f"/v1/knowledge-bases/{kb_id}/files/{file_id}/chunks/data_2",
        params={"context": 1},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["requestId"]
    data = payload["data"]
    target = data["target"]
    assert target["dataId"] == "data_2"
    assert target["text"] == "chunk text 2"
    assert target["charCount"] == len("chunk text 2")
    assert target["page"] == 3
    assert target["chunkIndex"] == 2
    assert target["citation"] == {"source": "ctx-test", "ordinal": 2}
    assert len(target["indexes"]) == 1
    assert target["indexes"][0] == {
        "indexId": "idx_data_2",
        "text": "index for data_2",
    }
    assert "embedding" not in target["indexes"][0]

    before = data["context"]["before"]
    after = data["context"]["after"]
    assert len(before) == 1
    assert before[0] == {
        "dataId": "data_1",
        "chunkIndex": 1,
        "page": 2,
        "text": "chunk text 1",
    }
    assert len(after) == 1
    assert after[0] == {
        "dataId": "data_3",
        "chunkIndex": 3,
        "page": 4,
        "text": "chunk text 3",
    }

    wide_response = client.get(
        f"/v1/knowledge-bases/{kb_id}/files/{file_id}/chunks/data_2",
        params={"context": 2},
    )
    assert wide_response.status_code == 200
    wide_before = wide_response.json()["data"]["context"]["before"]
    wide_after = wide_response.json()["data"]["context"]["after"]
    assert [item["dataId"] for item in wide_before] == ["data_0", "data_1"]
    assert [item["chunkIndex"] for item in wide_before] == [0, 1]
    assert [item["dataId"] for item in wide_after] == ["data_3", "data_4"]
    assert [item["chunkIndex"] for item in wide_after] == [3, 4]

    missing_data = client.get(
        f"/v1/knowledge-bases/{kb_id}/files/{file_id}/chunks/data_missing",
    )
    assert missing_data.status_code == 404
    assert missing_data.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
