from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.services.rag.embedder import HashEmbeddingService
from app.services.rag.vector_store import DataRecord, IndexRecord, JsonVectorStore, PgVectorStore

_EMBEDDER = HashEmbeddingService(dimensions=256)


def _build_merged_search_fixture(
    *,
    knowledge_base: str,
    file_id: str,
) -> tuple[list[DataRecord], list[IndexRecord], list[float]]:
    text_a = "alpha retrieval topic one"
    text_b = "beta unrelated content"
    emb_a1 = _EMBEDDER.embed(f"{text_a} segment one")
    emb_a2 = _EMBEDDER.embed(f"{text_a} segment two")
    emb_b = _EMBEDDER.embed(text_b)
    query_embedding = _EMBEDDER.embed(text_a)
    data_records = [
        DataRecord(
            knowledge_base=knowledge_base,
            file_id=file_id,
            data_id="d000000",
            text=text_a,
            page=1,
            chunk_index=0,
            citation={"file_name": "doc.txt", "page": 1, "data_id": "d000000"},
        ),
        DataRecord(
            knowledge_base=knowledge_base,
            file_id=file_id,
            data_id="d000001",
            text=text_b,
            page=2,
            chunk_index=1,
            citation={"file_name": "doc.txt", "page": 2, "data_id": "d000001"},
        ),
    ]
    index_records = [
        IndexRecord(
            knowledge_base=knowledge_base,
            file_id=file_id,
            data_id="d000000",
            index_id="d000000-000",
            text=f"{text_a} segment one",
            embedding=emb_a1,
        ),
        IndexRecord(
            knowledge_base=knowledge_base,
            file_id=file_id,
            data_id="d000000",
            index_id="d000000-001",
            text=f"{text_a} segment two",
            embedding=emb_a2,
        ),
        IndexRecord(
            knowledge_base=knowledge_base,
            file_id=file_id,
            data_id="d000001",
            index_id="d000001-000",
            text=text_b,
            embedding=emb_b,
        ),
    ]
    return data_records, index_records, query_embedding


def _assert_merged_search_results(
    hits: list[tuple[DataRecord, float]],
    *,
    file_id: str,
) -> None:
    assert len(hits) == 2
    first, second = hits
    assert first[0].data_id == "d000000"
    assert first[0].file_id == file_id
    assert first[0].text == "alpha retrieval topic one"
    assert first[0].citation["data_id"] == "d000000"
    assert second[0].data_id == "d000001"
    assert first[1] >= second[1]


def test_json_vector_store_search_data_merges_by_data_id(tmp_path) -> None:
    knowledge_base = "kb_json_merge"
    file_id = "file-1"
    store = JsonVectorStore(tmp_path)
    data_records, index_records, query_embedding = _build_merged_search_fixture(
        knowledge_base=knowledge_base,
        file_id=file_id,
    )
    store.upsert_data_index(
        knowledge_base,
        file_id,
        data_records=data_records,
        index_records=index_records,
    )

    hits = store.search_data(knowledge_base, query_embedding, top_k=5)

    _assert_merged_search_results(hits, file_id=file_id)
    data_ids = [record.data_id for record, _ in hits]
    assert data_ids.count("d000000") == 1

    store.delete_file(knowledge_base, file_id)
    assert store.search_data(knowledge_base, query_embedding, top_k=5) == []


@pytest.fixture
def pg_database_url() -> str:
    url = os.environ.get("DATABASE_URL") or Settings().DATABASE_URL
    if not url:
        pytest.skip("DATABASE_URL 未配置，跳过 PgVectorStore data/index 测试。")
    return url


def test_pg_vector_store_search_data_merges_by_data_id(pg_database_url: str) -> None:
    knowledge_base = f"kbtest_{uuid4().hex}"
    file_id = f"file_{uuid4().hex}"
    store = PgVectorStore(database_url=pg_database_url, dimensions=256)
    data_records, index_records, query_embedding = _build_merged_search_fixture(
        knowledge_base=knowledge_base,
        file_id=file_id,
    )
    try:
        store.upsert_data_index(
            knowledge_base,
            file_id,
            data_records=data_records,
            index_records=index_records,
        )
        hits = store.search_data(knowledge_base, query_embedding, top_k=5)
        _assert_merged_search_results(hits, file_id=file_id)
        data_ids = [record.data_id for record, _ in hits]
        assert data_ids.count("d000000") == 1
    finally:
        store.delete_file(knowledge_base, file_id)
        assert store.search_data(knowledge_base, query_embedding, top_k=5) == []
