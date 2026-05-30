from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from app.services.rag.parsing.base import (
    DocumentParser,
    ParsedBlock,
    ParsedDocument,
    ParsedImage,
)
from app.services.rag.parsing.image_store import ImageStore


def _suffix_from_image_name(name: str) -> str:
    suffix = Path(name).suffix
    if suffix:
        return suffix.lstrip(".").lower()
    return "bin"


def _pil_size(image_file: object) -> tuple[int | None, int | None]:
    pil_image = getattr(image_file, "image", None)
    if pil_image is None:
        return None, None
    return pil_image.size


class PdfDocumentParser(DocumentParser):
    extensions = (".pdf",)

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
            reader = PdfReader(str(file_path))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception as exc:
                    raise ValueError(
                        f"无法解析 PDF 文件 {file_path.name}: 文件已加密或密码错误"
                    ) from exc
            blocks: list[ParsedBlock] = []
            images: list[ParsedImage] = []
            page_texts: list[str] = []
            for page_number, page in enumerate(reader.pages, start=1):
                raw = page.extract_text() or ""
                text = raw.strip()
                page_texts.append(raw)
                blocks.append(ParsedBlock(page=page_number, text=text))
                if self._extract_images:
                    images.extend(
                        self._extract_page_images(page, page_number=page_number)
                    )
            full_text = "\n".join(page_texts)
            return ParsedDocument(full_text=full_text, blocks=blocks, images=images)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                f"无法解析 PDF 文件 {file_path.name}: {exc}"
            ) from exc

    def _extract_page_images(self, page: object, *, page_number: int) -> list[ParsedImage]:
        extracted: list[ParsedImage] = []
        page_images = getattr(page, "images", None)
        if page_images is None:
            return extracted
        for index_in_page, image_file in enumerate(page_images):
            try:
                data = image_file.data
                if not data:
                    continue
                name = getattr(image_file, "name", "") or f"image{index_in_page}.bin"
                storage_key = self._image_store.save(
                    data, suffix=_suffix_from_image_name(name)
                )
                width, height = _pil_size(image_file)
                extracted.append(
                    ParsedImage(
                        page=page_number,
                        storage_key=storage_key,
                        index_in_page=index_in_page,
                        width=width,
                        height=height,
                    )
                )
            except Exception:
                continue
        return extracted
