from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.import_job import ChunkingConfig, ParsingConfig
from app.services.rag.chunking_service import ChunkingService
from app.services.rag.parsing.docx_parser import DocxDocumentParser
from app.services.rag.parsing.image_store import ImageStore

_SAMPLE_DOCX = Path(
    "/Users/jackson/Downloads/f3f8168b-42c8-40c9-9e73-7a68d24466b3_RAG_Test_Introduce.docx"
)


@pytest.mark.skipif(not _SAMPLE_DOCX.is_file(), reason="本地示例 docx 不存在")
def test_rag_introduce_docx_splits_into_multiple_sections(tmp_path: Path) -> None:
    store = ImageStore(root=tmp_path)
    document = DocxDocumentParser(image_store=store).parse(_SAMPLE_DOCX)

    headings = [block.heading for block in document.blocks if block.heading]
    assert any("公共知识库" in (heading or "") for heading in headings)
    assert not any("数据索引" in (heading or "") for heading in headings)

    chunks = ChunkingService().split(
        document,
        ChunkingConfig(
            strategy="default",
            max_chunk_size=800,
            overlap=50,
            meta_headings=True,
            parsing=ParsingConfig(image_vlm_index=False),
        ),
    )
    assert len(chunks) >= 1
    assert any("公共知识库" in chunk.text for chunk in chunks)
    assert "![图示]" in chunks[0].text
    assert not any("数据索引" in chunk.text for chunk in chunks)
    assert not any("vector id" in chunk.text.casefold() for chunk in chunks)
