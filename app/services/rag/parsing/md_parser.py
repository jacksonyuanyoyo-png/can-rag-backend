from __future__ import annotations

import re
from pathlib import Path

from app.services.rag.parsing.base import DocumentParser, ParsedBlock, ParsedDocument

_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_PAGE_HEADING_RE = re.compile(r"^page\s+(\d+)$", re.IGNORECASE)
_IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def extract_image_storage_keys(text: str) -> list[str]:
    """从 Markdown 文本中提取 kb_images/ 下的图片 storage_key（去重、保序）。"""
    keys: list[str] = []
    seen: set[str] = set()
    for match in _IMAGE_REF_RE.finditer(text):
        target = match.group(2).strip()
        if target.startswith("kb_images/") and target not in seen:
            seen.add(target)
            keys.append(target)
    return keys


def parse_markdown_text(text: str) -> ParsedDocument:
    """将 Markdown 文本拆分为带 heading 的 ParsedBlock 列表。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return ParsedDocument(full_text="", blocks=[])

    blocks: list[ParsedBlock] = []
    current_heading: str | None = None
    current_page: int | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines
        body = "\n".join(current_lines).strip()
        if body or current_heading is not None:
            blocks.append(
                ParsedBlock(
                    page=current_page,
                    text=body,
                    heading=current_heading,
                )
            )
        current_lines = []

    for line in normalized.split("\n"):
        heading_match = _HEADING_LINE_RE.match(line)
        if heading_match:
            flush()
            current_heading = heading_match.group(2).strip()
            page_match = _PAGE_HEADING_RE.match(current_heading)
            current_page = int(page_match.group(1)) if page_match else None
            continue
        current_lines.append(line)

    flush()

    if not blocks:
        stripped = normalized.strip()
        return ParsedDocument(full_text=stripped, blocks=[ParsedBlock(page=None, text=stripped)])

    return ParsedDocument(
        full_text=normalized.strip(),
        blocks=blocks,
    )


def looks_like_markdown(text: str) -> bool:
    return bool(_HEADING_LINE_RE.search(text))


class MarkdownDocumentParser(DocumentParser):
    extensions = (".md", ".markdown")

    def parse(self, path: str | Path) -> ParsedDocument:
        file_path = Path(path)
        try:
            raw = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = file_path.read_text(encoding="utf-8", errors="replace")
        return parse_markdown_text(raw)
