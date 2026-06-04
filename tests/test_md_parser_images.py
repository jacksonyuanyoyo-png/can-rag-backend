from __future__ import annotations

from pathlib import Path

from app.services.rag.parsing.image_store import ImageStore
from app.services.rag.parsing.md_parser import (
    MarkdownDocumentParser,
    extract_image_storage_keys,
    parse_markdown_file,
)
from app.services.rag.pipeline import _HashRagPipeline
from app.services.rag.vector_store import JsonVectorStore
from app.core.config import Settings
from app.domain.import_job import ChunkingConfig
from app.services.rag.vlm_service import VlmService

MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_ingest_relative_markdown_image(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "diagram.png").write_bytes(MINIMAL_PNG)

    md_path = tmp_path / "guide.md"
    md_path.write_text(
        "# 标题\n\n说明文字。\n\n![布局图](./assets/diagram.png)\n\n后续段落。",
        encoding="utf-8",
    )

    store = ImageStore(upload_root)
    document = parse_markdown_file(md_path, image_store=store, upload_root=upload_root)

    assert len(document.images) == 1
    key = document.images[0].storage_key
    assert key.startswith("kb_images/")
    assert (upload_root / key).is_file()
    assert f"![布局图]({key})" in document.full_text
    assert extract_image_storage_keys(document.full_text) == [key]


def test_markdown_multimodal_index_with_vlm(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    (img_dir / "fig.png").write_bytes(MINIMAL_PNG)

    md_path = tmp_path / "doc.md"
    md_path.write_text(
        "## 章节\n\n键盘说明。\n\n![键位](imgs/fig.png)\n",
        encoding="utf-8",
    )

    store = ImageStore(upload_root)
    document = MarkdownDocumentParser(image_store=store, upload_root=upload_root).parse(
        md_path
    )
    settings = Settings(
        LOCAL_UPLOAD_ROOT=str(upload_root),
        RAG_BACKEND="local",
        LOCAL_VECTOR_STORE_PATH=str(tmp_path / "vectors"),
        VLM_ENABLED=True,
        RAG_EMBEDDING_DIMENSIONS=256,
    )
    pipeline = _HashRagPipeline(
        settings,
        JsonVectorStore(tmp_path / "vectors"),
        vlm_service=VlmService(
            settings,
            chat_completion=lambda messages: "键位说明：包含 Enter 与 Esc 键，用于确认与取消操作。",
        ),
    )
    counts = pipeline.index_data(
        knowledge_base="kb_md",
        file_id="file-md",
        document=document,
        config=ChunkingConfig(
            strategy="default",
            max_chunk_size=400,
            overlap=0,
            index_size=80,
            meta_headings=True,
        ),
        file_name="doc.md",
        force_image_description=True,
    )
    assert counts["images"] >= 1
    hits = pipeline.search_data(knowledge_base="kb_md", query="键位 Enter", top_k=5)
    assert any(h.citation.get("type") == "image" for h in hits)
