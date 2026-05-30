from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ParsedBlock:
    page: int | None
    text: str
    heading: str | None = None


@dataclass(slots=True)
class ParsedImage:
    page: int | None
    storage_key: str
    index_in_page: int = 0
    width: int | None = None
    height: int | None = None


@dataclass(slots=True)
class ParsedDocument:
    full_text: str
    blocks: list[ParsedBlock] = field(default_factory=list)
    images: list[ParsedImage] = field(default_factory=list)


class DocumentParser(ABC):
    """文档解析统一接口，子类按文件类型扩展（txt/pdf/docx/pptx）。"""

    extensions: tuple[str, ...] = ()

    def supports(self, filename: str) -> bool:
        return Path(filename).suffix.lower() in self.extensions

    @abstractmethod
    def parse(self, path: str | Path) -> ParsedDocument:
        raise NotImplementedError

    def parse_bytes(self, data: bytes, *, filename: str | None = None) -> ParsedDocument:
        raise NotImplementedError(
            f"{type(self).__name__} 未实现 parse_bytes"
        )
