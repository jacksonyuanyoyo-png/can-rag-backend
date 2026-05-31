from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pypdf import PdfWriter

from app.api.schemas.import_job import CreateImportJobRequest, ParsingOptions
from app.core.config import Settings
from app.domain.import_job import ChunkingConfig, ParsingConfig
from app.services.rag.chunking_service import ChunkingService
from app.services.rag.parsing.image_store import ImageStore
from app.services.rag.parsing.md_parser import parse_markdown_text
from app.services.rag.parsing.pdf_to_markdown import (
    enhanced_markdown_path_for,
    format_page_markdown_with_image,
    is_sparse_pdf_text,
    is_usable_vlm_markdown,
    normalize_vlm_markdown,
    parse_pdf_with_options,
    rewrite_image_refs,
    split_pdf_into_page_bytes,
)
from app.services.rag.parsing.pdf_to_markdown import is_legacy_pdf_noise
from app.services.rag.vlm_service import (
    _PAGE_MARKDOWN_SYSTEM_PROMPT,
    _PDF_PAGE_MARKDOWN_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
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


def test_is_usable_vlm_markdown_rejects_refusal() -> None:
    assert is_usable_vlm_markdown("抱歉，我无法处理", min_chars=10) is False
    assert is_usable_vlm_markdown("键盘布局说明 " * 20, min_chars=80) is True


def test_normalize_vlm_markdown_strips_fence() -> None:
    raw = "```markdown\n# 标题\n\n正文\n```"
    assert normalize_vlm_markdown(raw) == "# 标题\n\n正文"


def test_rewrite_image_refs_replaces_placeholder() -> None:
    raw = "![键盘布局](placeholder)\n![图二]{placeholder}"
    rewritten = rewrite_image_refs(raw, "kb_images/abc.png", page_number=1)
    assert "![键盘布局](kb_images/abc.png)" in rewritten
    assert "![图二](kb_images/abc.png)" in rewritten
    assert "placeholder" not in rewritten


def test_is_legacy_pdf_noise_detects_garbage() -> None:
    assert is_legacy_pdf_noise("1212 BCLCLM 12 12 12") is True
    assert is_legacy_pdf_noise("键盘布局说明 " * 20) is False


def test_format_page_markdown_with_image_injects_description() -> None:
    formatted = format_page_markdown_with_image(
        "# 标题\n\n正文。",
        storage_key="kb_images/page1.png",
        page_number=1,
        image_description="84 键紧凑布局，Fn 层说明。",
    )
    assert "![Page 1](kb_images/page1.png)" in formatted
    assert "**图示内容**" in formatted
    assert "84 键紧凑布局" in formatted
    assert "# 标题" in formatted


def _page_number_from_messages(messages: list) -> int:
    user_content = messages[1]["content"]
    for item in user_content:
        if item.get("type") == "text" and "第" in item.get("text", ""):
            text = item["text"]
            if "第 1 页" in text:
                return 1
            if "第 2 页" in text:
                return 2
    raise AssertionError(f"unexpected messages: {messages}")


def test_pdf_enhancement_with_mocked_vlm(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scan.pdf"
    _make_blank_pdf(pdf_path, pages=2)
    upload_root = tmp_path / "uploads"
    settings = Settings(
        LOCAL_UPLOAD_ROOT=str(upload_root),
        OPENAI_API_KEY="sk-test",
        VLM_ENABLED=True,
        VLM_MIN_IMAGE_BYTES=1,
    )

    def fake_completion(messages: list) -> str:
        system = messages[0]["content"]
        if system == _SYSTEM_PROMPT:
            return "键位布局：84 键紧凑排列，含 Fn 层。"
        if system in {_PAGE_MARKDOWN_SYSTEM_PROMPT, _PDF_PAGE_MARKDOWN_SYSTEM_PROMPT}:
            page_number = _page_number_from_messages(messages)
            if page_number == 1:
                return "# 键盘布局\n\nMini84 键位说明。"
            return "## 快捷键\n\nFn 组合键列表。"
        raise AssertionError(f"unexpected system prompt: {system[:80]}")

    vlm = VlmService(settings, chat_completion=fake_completion)
    document = parse_pdf_with_options(
        pdf_path,
        text_extraction=True,
        pdf_enhancement=True,
        settings=settings,
        vlm_service=vlm,
        image_store=ImageStore(upload_root),
    )

    assert "Mini84" in document.full_text or "键位布局" in document.full_text
    assert "快捷键" in document.full_text or "Fn" in document.full_text
    assert len(document.blocks) >= 2
    assert len(document.images) == 2
    assert all(image.storage_key.startswith("kb_images/") for image in document.images)
    assert "kb_images/" in document.full_text
    assert "placeholder" not in document.full_text
    if "**图示内容**" in document.full_text:
        assert "键位布局" in document.full_text

    markdown_path = enhanced_markdown_path_for(pdf_path)
    assert markdown_path.exists()
    saved = markdown_path.read_text(encoding="utf-8")
    assert "键位布局" in saved or "Mini84" in saved
    assert "Fn" in saved or "快捷键" in saved
    assert "## Page 1" in saved
    assert "## Page 2" in saved


def test_pdf_enhancement_saves_markdown_next_to_uploaded_pdf(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    kb_dir = upload_root / "kb" / "6238201e-5f6d-40b7-afcf-2fe6a7700302"
    kb_dir.mkdir(parents=True)
    pdf_path = kb_dir / "file_436c0ec573d14567bd209d930395201a.pdf"
    _make_blank_pdf(pdf_path)
    settings = Settings(
        LOCAL_UPLOAD_ROOT=str(upload_root),
        OPENAI_API_KEY="sk-test",
        VLM_ENABLED=True,
        VLM_MIN_IMAGE_BYTES=1,
    )
    vlm = VlmService(
        settings,
        chat_completion=lambda _messages: "# 标题\n\n正文内容。" * 5,
    )

    parse_pdf_with_options(
        pdf_path,
        text_extraction=False,
        pdf_enhancement=True,
        settings=settings,
        vlm_service=vlm,
    )

    markdown_path = kb_dir / "file_436c0ec573d14567bd209d930395201a.md"
    assert markdown_path.exists()
    assert "正文内容" in markdown_path.read_text(encoding="utf-8")


def test_pdf_enhancement_raises_when_convert_fails_and_legacy_is_noise(tmp_path: Path) -> None:
    pdf_path = tmp_path / "fallback.pdf"
    _make_blank_pdf(pdf_path)
    settings = Settings(
        OPENAI_API_KEY="sk-test",
        VLM_ENABLED=True,
        VLM_MIN_IMAGE_BYTES=1,
    )

    vlm = VlmService(settings, chat_completion=lambda _messages: "fallback markdown")

    with patch(
        "app.services.rag.parsing.pdf_to_markdown.PdfToMarkdownConverter.convert",
        side_effect=RuntimeError("convert failed"),
    ):
        with pytest.raises(ValueError, match="PDF 解析增强失败"):
            parse_pdf_with_options(
                pdf_path,
                text_extraction=True,
                pdf_enhancement=True,
                settings=settings,
                vlm_service=vlm,
            )

    assert not enhanced_markdown_path_for(pdf_path).exists()


def test_auto_enhance_when_text_sparse_and_vlm_enabled(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sparse.pdf"
    _make_blank_pdf(pdf_path)
    upload_root = tmp_path / "uploads_auto"
    settings = Settings(
        LOCAL_UPLOAD_ROOT=str(upload_root),
        VLM_ENABLED=True,
        VLM_MIN_IMAGE_BYTES=1,
        PDF_AUTO_ENHANCE_MIN_CHARS=100,
    )
    vlm = VlmService(
        settings,
        chat_completion=lambda _messages: "## Page 1\n\n增强后的正文内容。" * 5,
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

    markdown_path = enhanced_markdown_path_for(pdf_path)
    assert markdown_path.exists()
    assert "增强后的正文内容" in markdown_path.read_text(encoding="utf-8")


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


def test_split_pdf_into_page_bytes(tmp_path: Path) -> None:
    pdf_path = tmp_path / "multi.pdf"
    _make_blank_pdf(pdf_path, pages=3)

    pages = split_pdf_into_page_bytes(pdf_path.read_bytes())

    assert len(pages) == 3
    assert all(page.startswith(b"%PDF") for page in pages)
