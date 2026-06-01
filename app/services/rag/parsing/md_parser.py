from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.services.rag.parsing.base import (
    DocumentParser,
    ParsedBlock,
    ParsedDocument,
    ParsedImage,
)
from app.services.rag.parsing.image_store import ImageStore, KB_IMAGES_SUBDIR

_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_PAGE_HEADING_RE = re.compile(r"^page\s+(\d+)$", re.IGNORECASE)
_IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_GLUE_IMAGE_PARAGRAPH_RE = re.compile(r"\n\n(!\[[^\]]*\]\([^)]+\))")


def extract_image_storage_keys(text: str) -> list[str]:
    """从 Markdown 文本中提取 kb_images/ 下的图片 storage_key（去重、保序）。"""
    keys: list[str] = []
    seen: set[str] = set()
    for match in _IMAGE_REF_RE.finditer(text):
        target = match.group(2).strip()
        if target.startswith(f"{KB_IMAGES_SUBDIR}/") and target not in seen:
            seen.add(target)
            keys.append(target)
    return keys


def glue_images_to_paragraphs(text: str) -> str:
    """将图片行与上一段正文合并，避免分块时把图示与说明拆散。"""
    return _GLUE_IMAGE_PARAGRAPH_RE.sub(r"\n\1", text)


def parse_markdown_text(text: str) -> ParsedDocument:
    """将 Markdown 文本拆分为带 heading 的 ParsedBlock 列表。"""
    normalized = glue_images_to_paragraphs(
        text.replace("\r\n", "\n").replace("\r", "\n")
    )
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


def _is_remote_ref(ref: str) -> bool:
    lowered = ref.strip().lower()
    return lowered.startswith(("http://", "https://", "data:"))


def _normalize_storage_key(ref: str) -> str | None:
    cleaned = ref.strip().replace("\\", "/")
    if cleaned.startswith(f"{KB_IMAGES_SUBDIR}/"):
        return cleaned
    parsed = urlparse(cleaned)
    if parsed.scheme in {"", "file"}:
        path = unquote(parsed.path if parsed.scheme == "file" else cleaned)
        if path.startswith(f"/{KB_IMAGES_SUBDIR}/"):
            return path.lstrip("/")
        if path.startswith(f"{KB_IMAGES_SUBDIR}/"):
            return path
    return None


def _resolve_local_image_path(ref: str, *, md_path: Path, upload_root: Path) -> Path | None:
    if _is_remote_ref(ref):
        return None
    storage_key = _normalize_storage_key(ref)
    if storage_key is not None:
        candidate = upload_root / storage_key
        return candidate if candidate.is_file() else None

    raw = ref.strip().replace("\\", "/")
    if raw.startswith("/"):
        absolute = Path(raw)
        return absolute if absolute.is_file() else None

    relative = (md_path.parent / raw).resolve()
    if relative.is_file():
        return relative
    under_upload = (upload_root / raw).resolve()
    if under_upload.is_file():
        return under_upload
    return None


def _suffix_for_path(path: Path) -> str:
    suffix = path.suffix.lstrip(".").lower()
    return suffix or "png"


def ingest_markdown_images(
    text: str,
    *,
    md_path: Path,
    image_store: ImageStore,
    upload_root: Path,
) -> tuple[str, list[ParsedImage]]:
    """解析 Markdown 中的本地图片引用，复制到 kb_images 并改写链接。"""
    upload_root = upload_root.resolve()
    md_path = md_path.resolve()
    images: list[ParsedImage] = []
    key_by_ref: dict[str, str] = {}
    index_in_doc = 0

    def replace_ref(match: re.Match[str]) -> str:
        nonlocal index_in_doc
        alt = match.group(1)
        ref = match.group(2).strip()
        if _is_remote_ref(ref):
            return match.group(0)
        if ref in key_by_ref:
            storage_key = key_by_ref[ref]
        else:
            local_path = _resolve_local_image_path(
                ref, md_path=md_path, upload_root=upload_root
            )
            if local_path is None:
                return match.group(0)
            existing_key = _normalize_storage_key(ref)
            if existing_key is not None and (upload_root / existing_key).is_file():
                storage_key = existing_key
            else:
                storage_key = image_store.save(
                    local_path.read_bytes(),
                    suffix=_suffix_for_path(local_path),
                )
            key_by_ref[ref] = storage_key
            images.append(
                ParsedImage(
                    page=None,
                    storage_key=storage_key,
                    index_in_page=index_in_doc,
                )
            )
            index_in_doc += 1
        return f"![{alt}]({storage_key})"

    rewritten = _IMAGE_REF_RE.sub(replace_ref, text)
    return rewritten, images


def parse_markdown_file(
    path: str | Path,
    *,
    image_store: ImageStore | None = None,
    upload_root: Path | None = None,
    ingest_images: bool = True,
) -> ParsedDocument:
    file_path = Path(path)
    try:
        raw = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = file_path.read_text(encoding="utf-8", errors="replace")

    images: list[ParsedImage] = []
    if ingest_images and image_store is not None:
        root = upload_root if upload_root is not None else image_store._root
        raw, images = ingest_markdown_images(
            raw,
            md_path=file_path,
            image_store=image_store,
            upload_root=root,
        )

    document = parse_markdown_text(raw)
    if not images:
        return document
    return ParsedDocument(
        full_text=document.full_text,
        blocks=document.blocks,
        images=images,
    )


class MarkdownDocumentParser(DocumentParser):
    extensions = (".md", ".markdown")

    def __init__(
        self,
        *,
        image_store: ImageStore | None = None,
        upload_root: Path | None = None,
        ingest_images: bool = True,
    ) -> None:
        self._image_store = image_store
        self._upload_root = upload_root
        self._ingest_images = ingest_images

    def parse(self, path: str | Path) -> ParsedDocument:
        return parse_markdown_file(
            path,
            image_store=self._image_store,
            upload_root=self._upload_root,
            ingest_images=self._ingest_images and self._image_store is not None,
        )
