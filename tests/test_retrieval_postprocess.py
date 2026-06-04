from __future__ import annotations

from app.domain.knowledge_base import SearchHit
from app.services.rag.retrieval_postprocess import postprocess_search_hits


def _hit(
    *,
    chunk_id: str,
    text: str,
    score: float,
    chunk_type: str = "text",
    storage_key: str | None = None,
) -> SearchHit:
    citation: dict = {"type": chunk_type}
    if storage_key:
        citation["storage_key"] = storage_key
    return SearchHit(
        document_id="file_1",
        file_name="demo.docx",
        chunk_id=chunk_id,
        text=text,
        score=score,
        citation=citation,
    )


def test_filters_refusal_image_hits() -> None:
    hits = [
        _hit(
            chunk_id="img0000-000",
            text="请上传或提供具体的文档页面图片内容，我将帮助提取。",
            score=0.9,
            chunk_type="image",
            storage_key="kb_images/a.png",
        ),
        _hit(chunk_id="d000000", text="正文带图", score=0.5, storage_key="kb_images/a.png"),
    ]
    result = postprocess_search_hits(hits, top_k=5)
    assert len(result) == 1
    assert result[0].chunk_id == "d000000"


def test_drops_standalone_image_hits_when_text_chunks_exist() -> None:
    hits = [
        _hit(
            chunk_id="img0000-001",
            text="VLM 编造的菜单列表",
            score=0.95,
            chunk_type="image",
            storage_key="kb_images/menu.jpeg",
        ),
        _hit(
            chunk_id="d000000",
            text="步骤 ![图示](kb_images/menu.jpeg)",
            score=0.6,
            chunk_type="text",
            storage_key="kb_images/menu.jpeg",
        ),
    ]
    result = postprocess_search_hits(hits, top_k=5)
    assert len(result) == 1
    assert result[0].chunk_id == "d000000"


def test_dedupes_image_when_text_covers_storage_key() -> None:
    hits = [
        _hit(
            chunk_id="img0000-001",
            text="VLM 菜单描述",
            score=0.8,
            chunk_type="image",
            storage_key="kb_images/menu.jpeg",
        ),
        _hit(
            chunk_id="d000000",
            text="步骤 ![图示](kb_images/menu.jpeg)",
            score=0.7,
            storage_key="kb_images/menu.jpeg",
        ),
    ]
    result = postprocess_search_hits(hits, top_k=5)
    assert [item.chunk_id for item in result] == ["d000000"]
