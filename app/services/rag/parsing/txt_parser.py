from __future__ import annotations

import re
from pathlib import Path

from app.services.rag.parsing.base import DocumentParser, ParsedBlock, ParsedDocument

_PARAGRAPH_SEPARATOR = re.compile(r"\n\s*\n")


class TxtDocumentParser(DocumentParser):
    """纯文本解析器，迁移自知识库服务的 _read_text 编码兜底逻辑。"""

    extensions = (".txt", ".text")

    def parse(self, path: str | Path) -> ParsedDocument:
        return self._to_document(self._read_text(Path(path)))

    def parse_bytes(self, data: bytes, *, filename: str | None = None) -> ParsedDocument:
        return self._to_document(self._decode(data))

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _decode(data: bytes) -> str:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")

    @staticmethod
    def _to_document(text: str) -> ParsedDocument:
        blocks = [
            ParsedBlock(page=None, text=paragraph.strip())
            for paragraph in _PARAGRAPH_SEPARATOR.split(text)
            if paragraph.strip()
        ]
        return ParsedDocument(full_text=text, blocks=blocks)
