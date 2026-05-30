from app.services.rag.parsing.base import DocumentParser, ParsedBlock, ParsedDocument
from app.services.rag.parsing.docx_parser import DocxDocumentParser
from app.services.rag.parsing.pdf_parser import PdfDocumentParser
from app.services.rag.parsing.pptx_parser import PptxDocumentParser
from app.services.rag.parsing.txt_parser import TxtDocumentParser

_PARSERS: tuple[DocumentParser, ...] = (
    TxtDocumentParser(),
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
    "PdfDocumentParser",
    "DocxDocumentParser",
    "PptxDocumentParser",
    "get_parser_for",
]
