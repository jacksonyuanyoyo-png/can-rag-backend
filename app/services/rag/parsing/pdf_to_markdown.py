from __future__ import annotations

import logging
import re
from pathlib import Path

import fitz

from app.core.config import Settings
from app.services.rag.parsing.base import ParsedBlock, ParsedDocument, ParsedImage
from app.services.rag.parsing.image_store import ImageStore
from app.services.rag.parsing.md_parser import parse_markdown_text
from app.services.rag.parsing.pdf_parser import PdfDocumentParser
from app.services.rag.vlm_service import VlmService

logger = logging.getLogger(__name__)

_IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def is_sparse_pdf_text(text: str, *, min_chars: int) -> bool:
    meaningful = re.sub(r"\s+", "", text or "")
    return len(meaningful) < min_chars


def parse_pdf_with_options(
    path: str | Path,
    *,
    text_extraction: bool,
    pdf_enhancement: bool,
    settings: Settings,
    vlm_service: VlmService,
    image_store: ImageStore | None = None,
) -> ParsedDocument:
    file_path = Path(path)
    legacy_parser = PdfDocumentParser(image_store=image_store)
    store = image_store or ImageStore()

    legacy_doc: ParsedDocument | None = None
    if text_extraction:
        legacy_doc = legacy_parser.parse(file_path)

    use_enhancement = pdf_enhancement
    if not use_enhancement and legacy_doc is not None:
        if is_sparse_pdf_text(
            legacy_doc.full_text,
            min_chars=settings.PDF_AUTO_ENHANCE_MIN_CHARS,
        ):
            use_enhancement = settings.VLM_ENABLED

    if not use_enhancement:
        if legacy_doc is not None:
            return legacy_doc
        raise ValueError(f"PDF 解析未启用任何策略: {file_path.name}")

    if pdf_enhancement and not settings.OPENAI_API_KEY.strip():
        raise ValueError(
            f"PDF 解析增强需要配置 OPENAI_API_KEY: {file_path.name}"
        )

    try:
        converter = PdfToMarkdownConverter(
            settings=settings,
            vlm_service=vlm_service,
            image_store=store,
            force_vlm=pdf_enhancement,
        )
        return converter.convert(file_path)
    except Exception:
        logger.exception("PDF 解析增强失败，回退到 pypdf: %s", file_path.name)
        if legacy_doc is not None:
            return legacy_doc
        return legacy_parser.parse(file_path)


class PdfToMarkdownConverter:
    def __init__(
        self,
        *,
        settings: Settings,
        vlm_service: VlmService,
        image_store: ImageStore | None = None,
        force_vlm: bool = False,
    ) -> None:
        self._settings = settings
        self._vlm = vlm_service
        self._image_store = image_store or ImageStore()
        self._force_vlm = force_vlm

    def convert(self, path: str | Path) -> ParsedDocument:
        file_path = Path(path)
        doc = fitz.open(str(file_path))
        try:
            if doc.is_encrypted:
                if not doc.authenticate(""):
                    raise ValueError(f"无法解析 PDF 文件 {file_path.name}: 文件已加密或密码错误")

            page_markdowns: list[str] = []
            images: list[ParsedImage] = []

            for page_index in range(len(doc)):
                page_number = page_index + 1
                png_bytes = self._render_page_png(doc, page_index)
                storage_key = self._image_store.save(png_bytes, suffix="png")
                images.append(
                    ParsedImage(
                        page=page_number,
                        storage_key=storage_key,
                        index_in_page=0,
                    )
                )
                markdown = self._vlm.page_to_markdown(
                    png_bytes,
                    page_number=page_number,
                    force=self._force_vlm,
                )
                if not markdown.strip():
                    markdown = f"![Page {page_number}]({storage_key})"
                else:
                    markdown = self._rewrite_image_refs(markdown, storage_key, page_number)
                    if storage_key not in markdown:
                        markdown = f"{markdown}\n\n![Page {page_number}]({storage_key})"
                page_markdowns.append(f"## Page {page_number}\n\n{markdown.strip()}")

            combined = "\n\n".join(page_markdowns)
            parsed = parse_markdown_text(combined)
            return ParsedDocument(
                full_text=parsed.full_text,
                blocks=parsed.blocks or [
                    ParsedBlock(page=1, text=combined, heading=None),
                ],
                images=images,
            )
        finally:
            doc.close()

    def _render_page_png(self, doc: fitz.Document, page_index: int) -> bytes:
        page = doc[page_index]
        zoom = self._settings.PDF_RENDER_DPI / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        return pixmap.tobytes("png")

    @staticmethod
    def _rewrite_image_refs(markdown: str, storage_key: str, page_number: int) -> str:
        def _replace(match: re.Match[str]) -> str:
            alt = match.group(1)
            target = match.group(2).strip()
            if target.startswith("kb_images/"):
                return match.group(0)
            if target in {"placeholder", "image", "img"} or not target:
                return f"![{alt or f'Page {page_number}'}]({storage_key})"
            return match.group(0)

        return _IMAGE_REF_RE.sub(_replace, markdown)
