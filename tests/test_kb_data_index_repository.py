from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.repositories.kb_data_index_repository import KbDataIndexRepository


def _test_embedding() -> list[float]:
    dims = get_settings().RAG_EMBEDDING_DIMENSIONS
    vec = [0.0] * dims
    vec[0] = 0.1
    vec[1] = 0.2
    return vec


@pytest.fixture
def kb_data_index_repo(database_url: str, db_connection) -> KbDataIndexRepository:
    repo = KbDataIndexRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return repo


def _seed_file(repo: KbDataIndexRepository, kb_id: str, file_id: str) -> None:
    repo.ensure_kb_file_stub(kb_id=kb_id, file_id=file_id)


def test_upsert_query_and_delete_cascade(kb_data_index_repo: KbDataIndexRepository) -> None:
    kb_id = f"kb_kdi_{uuid4().hex[:8]}"
    file_id = f"file_kdi_{uuid4().hex[:8]}"
    data_id = "data_1"
    _seed_file(kb_data_index_repo, kb_id, file_id)

    citation = {"source": "unit-test", "page": 1}
    kb_data_index_repo.upsert_data(
        kb_id=kb_id,
        file_id=file_id,
        data_id=data_id,
        text="chunk one",
        page=1,
        chunk_index=0,
        citation=citation,
    )
    embedding = _test_embedding()
    kb_data_index_repo.upsert_index(
        kb_id=kb_id,
        file_id=file_id,
        data_id=data_id,
        index_id="idx_a",
        text="index a",
        embedding=embedding,
    )
    kb_data_index_repo.upsert_index(
        kb_id=kb_id,
        file_id=file_id,
        data_id=data_id,
        index_id="idx_b",
        text="index b",
        embedding=embedding,
    )

    data_rows = kb_data_index_repo.list_data_by_file(kb_id, file_id)
    assert len(data_rows) == 1
    assert data_rows[0]["data_id"] == data_id
    assert data_rows[0]["text"] == "chunk one"
    assert data_rows[0]["chunk_index"] == 0
    assert data_rows[0]["citation"] == citation

    loaded = kb_data_index_repo.get_data(kb_id, file_id, data_id)
    assert loaded is not None
    assert loaded["text"] == "chunk one"

    indexes = kb_data_index_repo.list_index_by_data(kb_id, file_id, data_id)
    assert len(indexes) == 2
    index_ids = {row["index_id"] for row in indexes}
    assert index_ids == {"idx_a", "idx_b"}
    assert len(indexes[0]["embedding"]) == get_settings().RAG_EMBEDDING_DIMENSIONS

    kb_data_index_repo.upsert_data(
        kb_id=kb_id,
        file_id=file_id,
        data_id=data_id,
        text="chunk one updated",
        page=2,
        chunk_index=0,
        citation={"source": "unit-test", "page": 2},
    )
    assert len(kb_data_index_repo.list_data_by_file(kb_id, file_id)) == 1
    updated = kb_data_index_repo.get_data(kb_id, file_id, data_id)
    assert updated is not None
    assert updated["text"] == "chunk one updated"
    assert updated["page"] == 2

    deleted_data = kb_data_index_repo.delete_by_data(kb_id, file_id, data_id)
    assert deleted_data == 1
    assert kb_data_index_repo.get_data(kb_id, file_id, data_id) is None
    assert kb_data_index_repo.list_index_by_data(kb_id, file_id, data_id) == []


def test_delete_by_file_clears_all(kb_data_index_repo: KbDataIndexRepository) -> None:
    kb_id = f"kb_kdi_file_{uuid4().hex[:8]}"
    file_id = f"file_kdi_file_{uuid4().hex[:8]}"
    _seed_file(kb_data_index_repo, kb_id, file_id)
    embedding = _test_embedding()

    kb_data_index_repo.bulk_upsert_data(
        [
            {
                "kb_id": kb_id,
                "file_id": file_id,
                "data_id": "data_x",
                "text": "x",
                "page": None,
                "chunk_index": 0,
                "citation": {},
            },
            {
                "kb_id": kb_id,
                "file_id": file_id,
                "data_id": "data_y",
                "text": "y",
                "page": None,
                "chunk_index": 1,
                "citation": {},
            },
        ]
    )
    kb_data_index_repo.bulk_upsert_index(
        [
            {
                "kb_id": kb_id,
                "file_id": file_id,
                "data_id": "data_x",
                "index_id": "idx_x1",
                "text": "vx1",
                "embedding": embedding,
            },
            {
                "kb_id": kb_id,
                "file_id": file_id,
                "data_id": "data_y",
                "index_id": "idx_y1",
                "text": "vy1",
                "embedding": embedding,
            },
        ]
    )

    assert len(kb_data_index_repo.list_data_by_file(kb_id, file_id)) == 2
    deleted = kb_data_index_repo.delete_by_file(kb_id, file_id)
    assert deleted == 2
    assert kb_data_index_repo.list_data_by_file(kb_id, file_id) == []
    assert kb_data_index_repo.list_index_by_data(kb_id, file_id, "data_x") == []
