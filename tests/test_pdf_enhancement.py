from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest
from pypdf import PdfWriter

from app.api.schemas.import_job import CreateImportJobRequest, ParsingOptions
from app.core.config import Settings
from app.domain.import_job import ChunkingConfig, ParsingConfig
from app.services.rag.chunking_service import ChunkingService
from app.services.rag.parsing.image_store import ImageStore
from app.services.rag.parsing.md_parser import parse_markdown_text
from app.services.rag.parsing.pdf_to_markdown import (
    is_sparse_pdf_text,
    parse_pdf_with_options,
)
from app.services.rag.vlm_service import VlmService


def _make_blank_pdf(path: Path, *, pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


def test_parsing_options_schema_parses_camel_case() -> None:
    req = CreateImportJobRequest.model_validate(
        {
            "fileIds": ["f1"],
            "parsing": {"textExtraction": True, "pdfEnhancement": True},
        }
    )

    assert req.parsing is not None
    assert req.parsing.text_extraction is True
    assert req.parsing.pdf_enhancement is True


def test_chunking_config_parsing_roundtrip() -> None:
    original = ChunkingConfig(
        strategy="default",
        parsing=ParsingConfig(text_extraction=False, pdf_enhancement=True),
    )

    restored = ChunkingConfig.from_dict(original.to_dict())

    assert restored.parsing.text_extraction is False
    assert restored.parsing.pdf_enhancement is True


def test_chunking_config_from_parsing_options() -> None:
    req = CreateImportJobRequest.model_validate(
        {
            "fileIds": ["f1"],
            "chunking": {"strategy": "default"},
            "parsing": {"textExtraction": True, "pdfEnhancement": True},
        }
    )
    config = ChunkingConfig.from_chunking_options(
        req.chunking,
        fallback_strategy=req.chunk_strategy,
        metadata=req.metadata,
        parsing=req.parsing,
    )

    assert config.parsing.pdf_enhancement is True


def test_parse_markdown_text_splits_by_headings() -> None:
    markdown = "## 概述\n\n第一段。\n\n## 细节\n\n第二段。"

    document = parse_markdown_text(markdown)

    assert len(document.blocks) == 2
    assert document.blocks[0].heading == "概述"
    assert "第一段" in document.blocks[0].text
    assert document.blocks[1].heading == "细节"


def test_markdown_default_chunking_splits_by_sections() -> None:
    markdown = "## 章节 A\n\n内容 A。\n\n## 章节 B\n\n内容 B。"
    document = parse_markdown_text(markdown)
    service = ChunkingService(settings=Settings(RAG_CHUNK_SIZE=800))
    config = ChunkingConfig(strategy="default")

    chunks = service.split(document, config)

    assert len(chunks) == 2
    assert "章节 A" in chunks[0].text
    assert "章节 B" in chunks[1].text


def test_is_sparse_pdf_text() -> None:
    assert is_sparse_pdf_text("abc", min_chars=100) is True
    assert is_sparse_pdf_text("x" * 120, min_chars=100) is False


def test_pdf_enhancement_with_mocked_vlm(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scan.pdf"
    _make_blank_pdf(pdf_path, pages=2)
    upload_root = tmp_path / "uploads"
    settings = Settings(
        LOCAL_UPLOAD_ROOT=str(upload_root),
        OPENAI_API_KEY="sk-test",
        VLM_ENABLED=True,
        VLM_MIN_IMAGE_BYTES=1,
        PDF_RENDER_DPI=72,
    )

    def fake_completion(messages: list) -> str:
        user_text = messages[1]["content"][0]["text"]
        if "第 1 页" in user_text:
            return "# 键盘布局\n\nMini84 键位说明。"
        return "## 快捷键\n\nFn 组合键列表。"

    vlm = VlmService(settings, chat_completion=fake_completion)
    document = parse_pdf_with_options(
        pdf_path,
        text_extraction=True,
        pdf_enhancement=True,
        settings=settings,
        vlm_service=vlm,
        image_store=ImageStore(upload_root),
    )

    assert "Mini84" in document.full_text
    assert "快捷键" in document.full_text
    assert len(document.blocks) >= 2
    assert len(document.images) == 2
    assert all(image.storage_key.startswith("kb_images/") for image in document.images)


def test_pdf_enhancement_falls_back_to_pypdf_on_vlm_failure(tmp_path: Path) -> None:
    pdf_path = tmp_path / "fallback.pdf"
    _make_blank_pdf(pdf_path)
    settings = Settings(
        OPENAI_API_KEY="sk-test",
        VLM_ENABLED=True,
        VLM_MIN_IMAGE_BYTES=1,
        PDF_RENDER_DPI=72,
    )

    def failing_completion(_messages: list) -> str:
        raise RuntimeError("vlm down")

    vlm = VlmService(settings, chat_completion=failing_completion)

    with patch(
        "app.services.rag.parsing.pdf_to_markdown.PdfToMarkdownConverter.convert",
        side_effect=RuntimeError("render failed"),
    ):
        document = parse_pdf_with_options(
            pdf_path,
            text_extraction=True,
            pdf_enhancement=True,
            settings=settings,
            vlm_service=vlm,
        )

    assert document.blocks
    assert document.blocks[0].page == 1


def test_auto_enhance_when_text_sparse_and_vlm_enabled(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sparse.pdf"
    _make_blank_pdf(pdf_path)
    upload_root = tmp_path / "uploads_auto"
    settings = Settings(
        LOCAL_UPLOAD_ROOT=str(upload_root),
        VLM_ENABLED=True,
        VLM_MIN_IMAGE_BYTES=1,
        PDF_AUTO_ENHANCE_MIN_CHARS=100,
        PDF_RENDER_DPI=72,
    )
    vlm = VlmService(
        settings,
        chat_completion=lambda _messages: "## Page\n\n增强后的正文内容。",
    )

    document = parse_pdf_with_options(
        pdf_path,
        text_extraction=True,
        pdf_enhancement=False,
        settings=settings,
        vlm_service=vlm,
        image_store=ImageStore(upload_root),
    )

    assert "增强后的正文内容" in document.full_text


def test_pdf_enhancement_requires_openai_api_key(tmp_path: Path) -> None:
    pdf_path = tmp_path / "no-key.pdf"
    _make_blank_pdf(pdf_path)
    settings = Settings(VLM_ENABLED=False, OPENAI_API_KEY="")
    vlm = VlmService(settings)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        parse_pdf_with_options(
            pdf_path,
            text_extraction=True,
            pdf_enhancement=True,
            settings=settings,
            vlm_service=vlm,
        )


def test_render_page_png_produces_bytes(tmp_path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=72, height=72)
    buffer = BytesIO()
    single_page = fitz.open()
    single_page.insert_pdf(doc, from_page=0, to_page=0)
    single_page.save(buffer)
    single_page.close()
    doc.close()

    pdf_path = tmp_path / "render.pdf"
    pdf_path.write_bytes(buffer.getvalue())
    opened = fitz.open(str(pdf_path))
    page = opened[0]
    pixmap = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
    png = pixmap.tobytes("png")
    opened.close()

    assert png.startswith(b"\x89PNG")
