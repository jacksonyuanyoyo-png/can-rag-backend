from app.services.rag.parsing.base import DocumentParser, ParsedBlock, ParsedDocument
from app.services.rag.parsing.docx_parser import DocxDocumentParser
from app.services.rag.parsing.html_parser import HtmlDocumentParser
from app.services.rag.parsing.md_parser import MarkdownDocumentParser
from app.services.rag.parsing.pdf_parser import PdfDocumentParser
from app.services.rag.parsing.pptx_parser import PptxDocumentParser
from app.services.rag.parsing.txt_parser import TxtDocumentParser
from app.services.rag.parsing.web_parser import WebUrlDocumentParser

_PARSERS: tuple[DocumentParser, ...] = (
    TxtDocumentParser(),
    MarkdownDocumentParser(),
    HtmlDocumentParser(),
    PdfDocumentParser(),
    DocxDocumentParser(),
    PptxDocumentParser(),
)


def get_parser_for(filename: str) -> DocumentParser | None:
    for parser in _PARSERS:
        if parser.supports(filename):
            return parser
    return None


__all__ = [
    "DocumentParser",
    "ParsedBlock",
    "ParsedDocument",
    "TxtDocumentParser",
    "MarkdownDocumentParser",
    "HtmlDocumentParser",
    "PdfDocumentParser",
    "DocxDocumentParser",
    "PptxDocumentParser",
    "get_parser_for",
    "WebUrlDocumentParser",
]
