from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.domain.import_job import ChunkingConfig
from app.services.rag.parsing.base import ParsedBlock, ParsedDocument, ParsedImage
from app.services.rag.parsing.image_store import ImageStore
from app.services.rag.pipeline import _HashRagPipeline
from app.services.rag.vector_store import JsonVectorStore
from app.services.rag.vlm_service import VlmService

_FAKE_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 5200)


def test_multimodal_index_and_search_with_injected_vlm(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    vector_root = tmp_path / "vectors"
    settings = Settings(
        LOCAL_UPLOAD_ROOT=str(upload_root),
        RAG_BACKEND="local",
        LOCAL_VECTOR_STORE_PATH=str(vector_root),
        VLM_ENABLED=True,
        VLM_MIN_IMAGE_BYTES=1,
        RAG_EMBEDDING_DIMENSIONS=256,
    )
    storage_key = ImageStore(upload_root).save(_FAKE_PNG_BYTES, suffix="png")
    document = ParsedDocument(
        full_text="正文关于检索的说明。",
        blocks=[ParsedBlock(page=1, text="正文关于检索的说明。")],
        images=[ParsedImage(page=1, storage_key=storage_key, index_in_page=0)],
    )
    fake_vlm = VlmService(
        settings,
        chat_completion=lambda messages: "流程图：步骤A→步骤B",
    )
    pipeline = _HashRagPipeline(
        settings,
        JsonVectorStore(vector_root),
        vlm_service=fake_vlm,
    )
    config = ChunkingConfig(
        strategy="default",
        max_chunk_size=200,
        overlap=10,
        index_size=80,
    )
    knowledge_base = "kb_multimodal"
    file_id = "file-mm-1"
    file_name = "doc-with-image.pdf"

    counts = pipeline.index_data(
        knowledge_base=knowledge_base,
        file_id=file_id,
        document=document,
        config=config,
        file_name=file_name,
    )

    assert counts["images"] >= 1
    assert counts["data"] >= counts["images"]

    hits = pipeline.search_data(
        knowledge_base=knowledge_base,
        query="流程图 步骤",
        top_k=5,
    )
    image_hits = [h for h in hits if h.citation.get("type") == "image"]
    assert image_hits
    citation = image_hits[0].citation
    assert citation.get("storage_key") == storage_key
    assert citation.get("page") == 1


def test_multimodal_skips_images_when_vlm_disabled(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads_off"
    vector_root = tmp_path / "vectors_off"
    settings = Settings(
        LOCAL_UPLOAD_ROOT=str(upload_root),
        RAG_BACKEND="local",
        LOCAL_VECTOR_STORE_PATH=str(vector_root),
        VLM_ENABLED=False,
        RAG_EMBEDDING_DIMENSIONS=256,
    )
    storage_key = ImageStore(upload_root).save(_FAKE_PNG_BYTES, suffix="png")
    document = ParsedDocument(
        full_text="仅文本。",
        blocks=[ParsedBlock(page=1, text="仅文本。")],
        images=[ParsedImage(page=1, storage_key=storage_key, index_in_page=0)],
    )
    pipeline = _HashRagPipeline(
        settings,
        JsonVectorStore(vector_root),
    )
    config = ChunkingConfig(
        strategy="default",
        max_chunk_size=200,
        overlap=10,
        index_size=80,
    )

    counts = pipeline.index_data(
        knowledge_base="kb_vlm_off",
        file_id="file-off",
        document=document,
        config=config,
        file_name="text-only.txt",
    )

    assert counts["images"] == 0
    assert counts["data"] == 1
