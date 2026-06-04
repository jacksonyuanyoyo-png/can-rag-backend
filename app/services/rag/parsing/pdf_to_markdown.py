from __future__ import annotations

import logging
import re
from io import BytesIO
from collections.abc import Callable
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from app.core.config import Settings
from app.services.rag.parsing.base import ParsedBlock, ParsedDocument, ParsedImage
from app.services.rag.parsing.image_store import ImageStore
from app.services.rag.parsing.md_parser import parse_markdown_text
from app.services.rag.parsing.pdf_parser import PdfDocumentParser
from app.services.rag.vlm_service import VlmService

logger = logging.getLogger(__name__)

_PDF_PAGE_IMAGE_HINT = (
    "这是说明书/文档页面截图。请用中文描述键位布局、功能图示、"
    "指示灯与快捷键等检索要点，输出简洁要点列表。"
)

_IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_BRACE_PLACEHOLDER_RE = re.compile(
    r"!\[([^\]]*)\]\{placeholder\}",
    re.IGNORECASE,
)

_REFUSAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(p, re.IGNORECASE)
    for p in (
        r"无法(将该|从该|把该|查看|处理)?",
        r"抱歉",
        r"对不起",
        r"不能提供",
        r"无法提供",
        r"是否有其他",
        r"没有文字或文档",
        r"没有.{0,12}内容可供",
        r"这是一张.{0,24}照片",
        r"USB设备的照片",
        r"如果能提供",
        r"尽力帮助",
        r"更清晰的图片",
    )
)

_LEGACY_NOISE_RE = re.compile(
    r"^(?:[\dA-Za-z]{1,6}\s*){2,}$",
    re.MULTILINE,
)


def is_sparse_pdf_text(text: str, *, min_chars: int) -> bool:
    meaningful = re.sub(r"\s+", "", text or "")
    return len(meaningful) < min_chars


def is_legacy_pdf_noise(text: str) -> bool:
    """pypdf 抽出的无意义碎片（如 1212 / BCLCLM），不可作为增强失败后的回退正文。"""
    stripped = re.sub(r"\s+", " ", (text or "").strip())
    if not stripped:
        return True
    if _LEGACY_NOISE_RE.fullmatch(stripped):
        return True
    tokens = stripped.split()
    if len(stripped) < 120 and len(tokens) <= 12:
        alpha_num = sum(1 for t in tokens if re.fullmatch(r"[\dA-Za-z]+", t))
        if alpha_num == len(tokens):
            return True
    return False


def _meaningful_char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", normalize_vlm_markdown(text)))


def is_usable_vlm_markdown(text: str, *, min_chars: int) -> bool:
    normalized = normalize_vlm_markdown(text)
    if _meaningful_char_count(normalized) < min_chars:
        return False
    if is_legacy_pdf_noise(normalized):
        return False
    return not any(pattern.search(normalized) for pattern in _REFUSAL_PATTERNS)


def page_markdown_min_chars(settings: Settings) -> int:
    return min(15, max(12, settings.PDF_VLM_MIN_MARKDOWN_CHARS // 6))


def enhanced_document_is_usable(
    document: ParsedDocument,
    *,
    settings: Settings,
) -> bool:
    text = document.full_text or ""
    min_total = settings.PDF_VLM_MIN_MARKDOWN_CHARS
    if is_usable_vlm_markdown(text, min_chars=min_total):
        return True
    if not document.images or "kb_images/" not in text:
        return False
    if is_legacy_pdf_noise(text):
        return False
    stripped = _IMAGE_REF_RE.sub("", text)
    stripped = re.sub(r"\*\*图示内容\*\*[:：]?", "", stripped)
    meaningful = re.sub(r"\s+", " ", stripped).strip()
    if _meaningful_char_count(meaningful) >= 25:
        return True
    return len(document.images) >= 2 and "图示内容" in text


def legacy_doc_is_fallback_safe(legacy_doc: ParsedDocument | None) -> bool:
    if legacy_doc is None:
        return False
    text = legacy_doc.full_text or ""
    if is_legacy_pdf_noise(text):
        return False
    return not is_sparse_pdf_text(text, min_chars=80)


def normalize_vlm_markdown(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:markdown)?\s*\n?", "", stripped)
        stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()


def enhanced_markdown_path_for(pdf_path: str | Path) -> Path:
    return Path(pdf_path).with_suffix(".md")


def save_enhanced_markdown(markdown: str, pdf_path: str | Path) -> Path:
    destination = enhanced_markdown_path_for(pdf_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(markdown, encoding="utf-8")
    logger.info("PDF 增强 Markdown 已保存: %s", destination)
    return destination


def render_pdf_page_png(pdf_path: Path, page_index: int, *, dpi: int) -> bytes:
    try:
        import pypdfium2 as pdfium
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "PDF 页面渲染需要 pypdfium2，请在运行 uvicorn 的 Python 环境中执行: "
            "pip install 'pypdfium2>=4.0.0,<5.0.0'"
        ) from exc

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_index]
        scale = dpi / 72.0
        bitmap = page.render(scale=scale)
        buffer = BytesIO()
        bitmap.to_pil().save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        doc.close()


def parse_pdf_with_options(
    path: str | Path,
    *,
    text_extraction: bool,
    pdf_enhancement: bool,
    settings: Settings,
    vlm_service: VlmService,
    image_store: ImageStore | None = None,
    on_page_progress: Callable[[int, int], None] | None = None,
) -> ParsedDocument:
    file_path = Path(path)
    legacy_doc: ParsedDocument | None = None
    if text_extraction:
        # 增强模式下不抽取 PDF 内嵌 jp2 等图，避免后续 Vision 报 invalid_image_format
        legacy_doc = PdfDocumentParser(
            image_store=image_store,
            extract_images=not pdf_enhancement,
        ).parse(file_path)

    use_enhancement = pdf_enhancement
    if not use_enhancement and legacy_doc is not None:
        if is_sparse_pdf_text(
            legacy_doc.full_text,
            min_chars=settings.PDF_AUTO_ENHANCE_MIN_CHARS,
        ):
            use_enhancement = settings.VLM_ENABLED

    if not use_enhancement:
        if legacy_doc is not None:
            if on_page_progress is not None and legacy_doc.blocks:
                pages = {
                    block.page
                    for block in legacy_doc.blocks
                    if block.page is not None
                }
                total = max(pages) if pages else 1
                on_page_progress(total, total)
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
            image_store=image_store or ImageStore(),
            force_vlm=pdf_enhancement,
        )
        enhanced = converter.convert(
            file_path,
            on_page_progress=on_page_progress,
        )
        if enhanced_document_is_usable(enhanced, settings=settings):
            return enhanced
        logger.warning(
            "PDF 增强结果过短或无效: %s (len=%d)",
            file_path.name,
            len(enhanced.full_text),
        )
        if legacy_doc_is_fallback_safe(legacy_doc):
            return legacy_doc
        raise ValueError(
            f"PDF 增强未能生成可用正文（请检查 OPENAI_API_KEY、PDF_VLM_MODEL 与 VLM 配置）: "
            f"{file_path.name}"
        )
    except ValueError:
        raise
    except Exception:
        logger.exception("PDF 解析增强失败: %s", file_path.name)
        if legacy_doc_is_fallback_safe(legacy_doc):
            return legacy_doc
        raise ValueError(
            f"PDF 解析增强失败: {file_path.name}"
        ) from None


def split_pdf_into_page_bytes(pdf_bytes: bytes) -> list[bytes]:
    reader = PdfReader(BytesIO(pdf_bytes))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValueError("文件已加密或密码错误") from exc

    pages: list[bytes] = []
    for page in reader.pages:
        writer = PdfWriter()
        writer.add_page(page)
        buffer = BytesIO()
        writer.write(buffer)
        pages.append(buffer.getvalue())
    return pages


class PdfToMarkdownConverter:
    def __init__(
        self,
        *,
        settings: Settings,
        vlm_service: VlmService,
        image_store: ImageStore,
        force_vlm: bool = False,
    ) -> None:
        self._settings = settings
        self._vlm = vlm_service
        self._image_store = image_store
        self._force_vlm = force_vlm

    def convert(
        self,
        path: str | Path,
        *,
        on_page_progress: Callable[[int, int], None] | None = None,
    ) -> ParsedDocument:
        file_path = Path(path)
        pdf_bytes = file_path.read_bytes()
        page_pdfs = split_pdf_into_page_bytes(pdf_bytes)
        if not page_pdfs:
            raise ValueError(f"PDF 文件没有可解析的页面: {file_path.name}")

        page_markdowns: list[str] = []
        images: list[ParsedImage] = []
        total_pages = len(page_pdfs)
        for page_index, page_pdf in enumerate(page_pdfs):
            page_number = page_index + 1
            markdown, page_image = self._convert_page(
                file_path,
                page_pdf=page_pdf,
                page_index=page_index,
                page_number=page_number,
            )
            images.append(page_image)
            if not markdown.strip():
                markdown = f"（第 {page_number} 页无文本内容）"
            page_markdowns.append(f"## Page {page_number}\n\n{markdown.strip()}")
            if on_page_progress is not None:
                on_page_progress(page_number, total_pages)

        combined = "\n\n".join(page_markdowns)
        save_enhanced_markdown(combined, file_path)
        parsed = parse_markdown_text(combined)
        return ParsedDocument(
            full_text=parsed.full_text,
            blocks=parsed.blocks or [
                ParsedBlock(page=1, text=combined, heading=None),
            ],
            images=images,
        )

    def _convert_page(
        self,
        file_path: Path,
        *,
        page_pdf: bytes,
        page_index: int,
        page_number: int,
    ) -> tuple[str, ParsedImage]:
        png_bytes = render_pdf_page_png(
            file_path,
            page_index,
            dpi=self._settings.PDF_RENDER_DPI,
        )
        storage_key = self._image_store.save(png_bytes, suffix="png")
        page_image = ParsedImage(
            page=page_number,
            storage_key=storage_key,
            index_in_page=0,
        )

        min_chars = page_markdown_min_chars(self._settings)
        markdown = ""

        # 扫描类说明书以页面截图 OCR 为主；单页 PDF 文件输入常返回拒答或乱码
        if self._force_vlm or self._settings.VLM_ENABLED:
            markdown = normalize_vlm_markdown(
                self._vlm.page_to_markdown(
                    png_bytes,
                    page_number=page_number,
                    force=True,
                    model=self._settings.PDF_VLM_MODEL,
                )
            )

        if not is_usable_vlm_markdown(markdown, min_chars=min_chars):
            pdf_markdown = normalize_vlm_markdown(
                self._vlm.pdf_page_to_markdown(
                    page_pdf,
                    page_number=page_number,
                    filename=f"{file_path.stem}_p{page_number}.pdf",
                    force=self._force_vlm,
                )
            )
            if is_usable_vlm_markdown(pdf_markdown, min_chars=min_chars):
                logger.info(
                    "页面 OCR 不足，改用 PDF 原生输入: %s page=%d",
                    file_path.name,
                    page_number,
                )
                markdown = pdf_markdown
            elif is_usable_vlm_markdown(markdown, min_chars=min_chars):
                pass
            else:
                logger.warning(
                    "页面 VLM 均无效，仅保留页图占位: %s page=%d preview=%r",
                    file_path.name,
                    page_number,
                    (markdown or pdf_markdown)[:120],
                )
                markdown = ""

        markdown = rewrite_image_refs(markdown, storage_key, page_number)
        image_description: str | None = None
        if self._force_vlm and self._settings.OPENAI_API_KEY.strip():
            raw_desc = self._vlm.describe_image(
                png_bytes,
                mime_type="image/png",
                hint=_PDF_PAGE_IMAGE_HINT,
                force=True,
                model=self._settings.PDF_VLM_MODEL,
            )
            if raw_desc and is_usable_vlm_markdown(raw_desc, min_chars=12):
                image_description = raw_desc
        markdown = format_page_markdown_with_image(
            markdown,
            storage_key=storage_key,
            page_number=page_number,
            image_description=image_description,
        )
        return markdown, page_image


def format_page_markdown_with_image(
    markdown: str,
    *,
    storage_key: str,
    page_number: int,
    image_description: str | None,
) -> str:
    """在页 Markdown 中保留图片引用，并可选附加 VLM 图示要点供检索与原文对照。"""
    img_line = f"![Page {page_number}]({storage_key})"
    body = markdown.strip()
    desc = (image_description or "").strip()
    desc_block = f"\n\n**图示内容**：\n{desc}\n\n" if desc else ""

    if body.startswith(img_line):
        rest = body[len(img_line) :].lstrip()
        if desc and "**图示内容**" not in body:
            return f"{img_line}{desc_block}{rest}".rstrip()
        return body

    first_line, _, remainder = body.partition("\n")
    if first_line.startswith("![") and storage_key in first_line:
        if desc and "**图示内容**" not in body:
            tail = remainder.lstrip("\n")
            return f"{first_line}{desc_block}{tail}".rstrip()
        return body

    prefix = f"{img_line}{desc_block}" if desc else f"{img_line}\n\n"
    return f"{prefix}{body}".rstrip()


def rewrite_image_refs(
    markdown: str,
    storage_key: str,
    page_number: int,
) -> str:
    """将 VLM 占位图链接替换为 kb_images 下的真实 storage_key。"""

    def _replace_paren(match: re.Match[str]) -> str:
        alt = match.group(1)
        target = match.group(2).strip()
        if target.startswith("kb_images/"):
            return match.group(0)
        if target in {"placeholder", "image", "img"} or not target:
            return f"![{alt or f'Page {page_number}'}]({storage_key})"
        return match.group(0)

    def _replace_brace(match: re.Match[str]) -> str:
        alt = match.group(1)
        return f"![{alt or f'Page {page_number}'}]({storage_key})"

    normalized = _BRACE_PLACEHOLDER_RE.sub(_replace_brace, markdown)
    return _IMAGE_REF_RE.sub(_replace_paren, normalized)
