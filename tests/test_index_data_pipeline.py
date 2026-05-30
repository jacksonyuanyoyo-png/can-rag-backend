from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.domain.import_job import ChunkingConfig
from app.services.rag.pipeline import RagPipeline
from app.services.rag.parsing.base import ParsedBlock, ParsedDocument


@pytest.fixture
def pipeline(tmp_path: Path) -> RagPipeline:
    settings = Settings(
        DATABASE_URL="",
        RAG_BACKEND="local",
        RAG_EMBEDDING_DIMENSIONS=256,
    )
    settings.LOCAL_VECTOR_STORE_PATH = str(tmp_path / "vectors")
    return RagPipeline(settings=settings)


def test_index_data_and_search_data(pipeline: RagPipeline) -> None:
    paragraph_one = "第一段关于向量检索的内容。" * 40
    paragraph_two = "第二段补充说明与索引切分。" * 40
    document = ParsedDocument(
        full_text=f"{paragraph_one}\n\n{paragraph_two}",
        blocks=[
            ParsedBlock(page=1, text=paragraph_one, heading="章节一"),
            ParsedBlock(page=2, text=paragraph_two, heading="章节二"),
        ],
    )
    config = ChunkingConfig(
        strategy="default",
        max_chunk_size=120,
        overlap=10,
        index_size=80,
    )
    knowledge_base = "kb_pipeline"
    file_id = "file-pipeline-1"
    file_name = "pipeline-doc.txt"
    needle = "向量检索"

    counts = pipeline.index_data(
        knowledge_base=knowledge_base,
        file_id=file_id,
        document=document,
        config=config,
        file_name=file_name,
    )

    assert counts["data"] >= 1
    assert counts["index"] >= counts["data"]

    hits = pipeline.search_data(
        knowledge_base=knowledge_base,
        query=needle,
        top_k=3,
    )

    assert hits
    assert any(needle in hit.text for hit in hits)
    top = hits[0]
    assert top.document_id == file_id
    assert top.citation.get("file_name") == file_name
    assert top.citation.get("data_id")
