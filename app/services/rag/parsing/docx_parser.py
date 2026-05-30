from __future__ import annotations

from pathlib import Path

import docx

from app.services.rag.parsing.base import (
    DocumentParser,
    ParsedBlock,
    ParsedDocument,
    ParsedImage,
)
from app.services.rag.parsing.image_store import ImageStore


def _is_heading_paragraph(paragraph: docx.text.paragraph.Paragraph) -> bool:
    style = paragraph.style
    if style is None or style.name is None:
        return False
    return style.name.startswith("Heading")


def _suffix_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return "bin"
    if "/" in content_type:
        return content_type.rsplit("/", 1)[-1].lower() or "bin"
    return content_type.lstrip(".").lower() or "bin"


class DocxDocumentParser(DocumentParser):
    extensions = (".docx",)

    def __init__(
        self,
        *,
        extract_images: bool = True,
        image_store: ImageStore | None = None,
    ) -> None:
        self._extract_images = extract_images
        self._image_store = image_store or ImageStore()

    def parse(self, path: str | Path) -> ParsedDocument:
        file_path = Path(path)
        try:
            document = docx.Document(str(file_path))
            return self._to_document(document)
        except Exception as exc:
            raise ValueError(
                f"无法解析 DOCX 文件 {file_path.name}: {exc}"
            ) from exc

    def _to_document(self, document: docx.Document) -> ParsedDocument:
        blocks: list[ParsedBlock] = []
        paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
        full_text = "\n".join(paragraphs)

        current_heading: str | None = None
        current_body: list[str] = []

        def flush() -> None:
            nonlocal current_heading, current_body
            if current_heading is None and not current_body:
                return
            text = "\n".join(current_body)
            blocks.append(
                ParsedBlock(page=None, text=text, heading=current_heading)
            )
            current_heading = None
            current_body = []

        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            if _is_heading_paragraph(paragraph):
                flush()
                current_heading = text
            else:
                current_body.append(text)

        flush()

        if not blocks and full_text:
            blocks.append(ParsedBlock(page=None, text=full_text, heading=None))

        images: list[ParsedImage] = []
        if self._extract_images:
            images = self._extract_images_from_document(document)

        return ParsedDocument(full_text=full_text, blocks=blocks, images=images)

    def _extract_images_from_document(self, document: docx.Document) -> list[ParsedImage]:
        extracted: list[ParsedImage] = []
        index_in_page = 0
        for rel in document.part.rels.values():
            if "image" not in rel.reltype:
                continue
            try:
                part = rel.target_part
                blob = part.blob
                if not blob:
                    continue
                content_type = getattr(part, "content_type", None)
                storage_key = self._image_store.save(
                    blob, suffix=_suffix_from_content_type(content_type)
                )
                extracted.append(
                    ParsedImage(
                        page=None,
                        storage_key=storage_key,
                        index_in_page=index_in_page,
                    )
                )
                index_in_page += 1
            except Exception:
                continue
        return extracted
