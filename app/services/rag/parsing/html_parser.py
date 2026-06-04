from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings, get_settings
from app.services.rag.parsing.base import DocumentParser, ParsedDocument
from app.services.rag.parsing.md_parser import parse_markdown_text
from app.services.rag.parsing.web_extractor import extract_from_html
from app.services.rag.parsing.web_fetcher import WebFetchError

_SOURCE_URL_RE = re.compile(
    r"^Source\s+URL:\s*(https?://\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


class HtmlDocumentParser(DocumentParser):
    """浏览器「另存为网页」的 .html：抽取正文为 Markdown 后按 MD 分段。"""

    extensions = (".html", ".htm")

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def parse(self, path: str | Path) -> ParsedDocument:
        file_path = Path(path)
        try:
            raw = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = file_path.read_text(encoding="utf-8", errors="replace")
        source_url = _guess_source_url(raw) or f"file://{file_path.name}"
        try:
            extracted = extract_from_html(
                raw,
                url=source_url,
                settings=self._settings,
            )
        except WebFetchError as exc:
            raise ValueError(
                f"无法解析 HTML 文件 {file_path.name}: {exc}"
            ) from exc
        return parse_markdown_text(extracted.markdown)


def _guess_source_url(html: str) -> str | None:
    match = _SOURCE_URL_RE.search(html[:4000])
    if match:
        return match.group(1).strip()
    return None
