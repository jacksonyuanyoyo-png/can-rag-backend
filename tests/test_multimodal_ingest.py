from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.domain.import_job import ChunkingConfig
from app.services.rag.parsing.base import ParsedBlock, ParsedDocument, ParsedImage
from app.services.rag.parsing.image_store import ImageStore
from app.services.rag.pipeline import _HashRagPipeline
from app.services.rag.vector_store import JsonVectorStore
from app.services.rag.vlm_service import VlmService

MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


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
    storage_key = ImageStore(upload_root).save(MINIMAL_PNG, suffix="png")
    document = ParsedDocument(
        full_text="正文关于检索的说明。",
        blocks=[ParsedBlock(page=1, text="正文关于检索的说明。")],
        images=[ParsedImage(page=1, storage_key=storage_key, index_in_page=0)],
    )
    fake_vlm = VlmService(
        settings,
        chat_completion=lambda messages: "流程图说明：步骤 A 完成后进入步骤 B，形成顺序执行链路。",
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
        force_image_description=True,
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


def test_text_chunk_citation_includes_image_storage_key(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads_keys"
    vector_root = tmp_path / "vectors_keys"
    settings = Settings(
        LOCAL_UPLOAD_ROOT=str(upload_root),
        RAG_BACKEND="local",
        LOCAL_VECTOR_STORE_PATH=str(vector_root),
        VLM_ENABLED=False,
        RAG_EMBEDDING_DIMENSIONS=256,
    )
    storage_key = "kb_images/sample.png"
    document = ParsedDocument(
        full_text=f"说明。\n\n![布局图]({storage_key})\n\n正文。",
        blocks=[
            ParsedBlock(
                page=1,
                text=f"说明。\n\n![布局图]({storage_key})\n\n正文。",
            )
        ],
        images=[],
    )
    pipeline = _HashRagPipeline(settings, JsonVectorStore(vector_root))
    config = ChunkingConfig(strategy="default", max_chunk_size=500, overlap=0, index_size=80)

    pipeline.index_data(
        knowledge_base="kb_keys",
        file_id="file-keys",
        document=document,
        config=config,
        file_name="with-image.md",
    )
    hits = pipeline.search_data(knowledge_base="kb_keys", query="布局图", top_k=3)
    text_hits = [h for h in hits if h.citation.get("type") != "image"]
    assert text_hits
    assert text_hits[0].citation.get("storage_key") == storage_key


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
    storage_key = ImageStore(upload_root).save(MINIMAL_PNG, suffix="png")
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
