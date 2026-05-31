from __future__ import annotations

from app.core.config import Settings
from app.services.rag.parsing.base import ParsedDocument
from app.services.rag.parsing.md_parser import parse_markdown_text
from app.services.rag.parsing.web_extractor import WebExtractionResult, extract_from_url


class WebUrlDocumentParser:
    """通用网页 URL → ParsedDocument（无站点定制）。"""

    def fetch_and_parse(
        self,
        url: str,
        *,
        settings: Settings,
        use_browser_fallback: bool | None = None,
    ) -> ParsedDocument:
        result = extract_from_url(
            url,
            settings=settings,
            use_browser_fallback=use_browser_fallback,
        )
        return parsed_document_from_extraction(result)

    @staticmethod
    def from_extraction(result: WebExtractionResult) -> ParsedDocument:
        return parsed_document_from_extraction(result)


def parsed_document_from_extraction(result: WebExtractionResult) -> ParsedDocument:
    return parse_markdown_text(result.markdown)
