from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from langchain_text_splitters import CharacterTextSplitter, RecursiveCharacterTextSplitter

from app.core.config import Settings, get_settings
from app.domain.import_job import ChunkingConfig
from app.services.rag.parsing.base import ParsedBlock, ParsedDocument
from app.services.rag.parsing.md_parser import glue_images_to_paragraphs

_PARAGRAPH_SEPARATORS: tuple[str, ...] = ("\n\n", "\n", "。", "．", ". ", " ", "")


@dataclass(frozen=True, slots=True)
class DataChunk:
    text: str
    page: int | None
    chunk_index: int


@dataclass(frozen=True, slots=True)
class IndexChunk:
    data_chunk_index: int
    index_in_data: int
    text: str
    raw_text: str
    page: int | None
    citation: dict


class ChunkingService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def split(self, document: ParsedDocument, config: ChunkingConfig) -> list[DataChunk]:
        strategy = config.strategy
        if strategy == "whole":
            raise ValueError("strategy=whole 不支持分块")
        if strategy == "default":
            return self._split_default(document, config)
        if strategy == "page":
            return self._split_page(document, config)
        if strategy == "custom":
            mode = config.mode
            if mode == "paragraph":
                return self._split_paragraph(document, config)
            if mode == "length":
                return self._split_length(document, config)
            if mode == "separator":
                return self._split_separator(document, config)
            raise ValueError(f"未知的 custom 分块模式: {mode}")
        raise ValueError(f"未知的分块策略: {strategy}")

    def build_indexes(
        self,
        data_chunks: list[DataChunk],
        config: ChunkingConfig,
        *,
        file_name: str | None = None,
        data_id_of: Callable[[DataChunk], str] | None = None,
        heading_of: Callable[[DataChunk], str | None] | None = None,
    ) -> list[IndexChunk]:
        resolve_data_id = data_id_of or (lambda chunk: str(chunk.chunk_index))
        indexes: list[IndexChunk] = []
        for data_chunk in data_chunks:
            raw_parts = self._split_index_texts(data_chunk.text, config.index_size)
            heading = heading_of(data_chunk) if heading_of is not None else None
            data_id = resolve_data_id(data_chunk)
            citation = {
                "file_name": file_name,
                "page": data_chunk.page,
                "data_id": data_id,
            }
            for index_in_data, raw_text in enumerate(raw_parts):
                text = self._apply_index_prefix(
                    raw_text,
                    config=config,
                    file_name=file_name,
                    heading=heading,
                )
                indexes.append(
                    IndexChunk(
                        data_chunk_index=data_chunk.chunk_index,
                        index_in_data=index_in_data,
                        text=text,
                        raw_text=raw_text,
                        page=data_chunk.page,
                        citation=citation,
                    )
                )
        return indexes

    def split_and_index(
        self,
        document: ParsedDocument,
        config: ChunkingConfig,
        *,
        file_name: str | None = None,
        data_id_of: Callable[[DataChunk], str] | None = None,
        heading_of: Callable[[DataChunk], str | None] | None = None,
    ) -> tuple[list[DataChunk], list[IndexChunk]]:
        data_chunks = self.split(document, config)
        index_chunks = self.build_indexes(
            data_chunks,
            config,
            file_name=file_name,
            data_id_of=data_id_of,
            heading_of=heading_of,
        )
        return data_chunks, index_chunks

    @staticmethod
    def _split_index_texts(text: str, index_size: int) -> list[str]:
        stripped = text.strip()
        if not stripped:
            return []
        if len(stripped) <= index_size:
            return [stripped]
        return ChunkingService._recursive_split(
            stripped,
            chunk_size=index_size,
            chunk_overlap=0,
            separators=list(_PARAGRAPH_SEPARATORS),
        )

    @staticmethod
    def _apply_index_prefix(
        raw_text: str,
        *,
        config: ChunkingConfig,
        file_name: str | None,
        heading: str | None,
    ) -> str:
        prefix_lines: list[str] = []
        if config.meta_filename and file_name:
            prefix_lines.append(f"文件：{file_name}")
        if config.meta_headings and heading:
            prefix_lines.append(f"标题：{heading}")
        if not prefix_lines:
            return raw_text
        return "\n".join(prefix_lines) + "\n" + raw_text

    def _split_default(self, document: ParsedDocument, config: ChunkingConfig) -> list[DataChunk]:
        if self._should_split_markdown(document):
            return self._split_markdown_sections(document, config)
        page = self._first_block_page(document.blocks)
        texts = self._recursive_split(
            glue_images_to_paragraphs(document.full_text),
            chunk_size=config.max_chunk_size or self._settings.RAG_CHUNK_SIZE,
            chunk_overlap=config.overlap
            if config.overlap is not None
            else self._settings.RAG_CHUNK_OVERLAP,
            separators=list(_PARAGRAPH_SEPARATORS),
        )
        return self._build_chunks(texts, page=page)

    @staticmethod
    def _should_split_markdown(document: ParsedDocument) -> bool:
        return any(block.heading for block in document.blocks)

    def _split_markdown_sections(
        self,
        document: ParsedDocument,
        config: ChunkingConfig,
    ) -> list[DataChunk]:
        max_size = config.max_chunk_size or self._settings.RAG_CHUNK_SIZE
        overlap = (
            config.overlap
            if config.overlap is not None
            else self._settings.RAG_CHUNK_OVERLAP
        )
        pieces: list[tuple[str, int | None]] = []
        for block in document.blocks:
            text = glue_images_to_paragraphs(block.text.strip())
            if not text:
                continue
            section = f"## {block.heading}\n\n{text}" if block.heading else text
            if len(section) <= max_size:
                pieces.append((section, block.page))
                continue
            for part in self._recursive_split(
                section,
                chunk_size=max_size,
                chunk_overlap=overlap,
                separators=list(_PARAGRAPH_SEPARATORS),
            ):
                pieces.append((part, block.page))
        return self._build_chunks_from_pairs(pieces)

    def _split_paragraph(self, document: ParsedDocument, config: ChunkingConfig) -> list[DataChunk]:
        _ = config.paragraph_use_model
        depth = config.paragraph_max_depth
        if depth is None or depth <= 0:
            separators = list(_PARAGRAPH_SEPARATORS)
        else:
            separators = list(_PARAGRAPH_SEPARATORS[:depth])
            if separators[-1] != "":
                separators.append("")
        page = self._first_block_page(document.blocks)
        texts = self._recursive_split(
            document.full_text,
            chunk_size=config.max_chunk_size or self._settings.RAG_CHUNK_SIZE,
            chunk_overlap=0,
            separators=separators,
        )
        return self._build_chunks(texts, page=page)

    def _split_length(self, document: ParsedDocument, config: ChunkingConfig) -> list[DataChunk]:
        page = self._first_block_page(document.blocks)
        texts = self._recursive_split(
            document.full_text,
            chunk_size=config.chunk_size or self._settings.RAG_CHUNK_SIZE,
            chunk_overlap=config.overlap
            if config.overlap is not None
            else self._settings.RAG_CHUNK_OVERLAP,
            separators=list(_PARAGRAPH_SEPARATORS),
        )
        return self._build_chunks(texts, page=page)

    def _split_separator(self, document: ParsedDocument, config: ChunkingConfig) -> list[DataChunk]:
        if not config.separators:
            raise ValueError("separator 模式需要 separators")
        page = self._first_block_page(document.blocks)
        chunk_size = config.max_chunk_size or self._settings.RAG_CHUNK_SIZE
        parts = self._split_by_separators(document.full_text, list(config.separators))
        texts: list[str] = []
        for part in parts:
            if len(part) <= chunk_size:
                texts.append(part)
                continue
            if len(config.separators) == 1:
                splitter = CharacterTextSplitter(
                    separator=config.separators[0],
                    chunk_size=chunk_size,
                    chunk_overlap=0,
                    is_separator_regex=False,
                )
                texts.extend(splitter.split_text(part))
            else:
                texts.extend(
                    self._recursive_split(
                        part,
                        chunk_size=chunk_size,
                        chunk_overlap=0,
                        separators=list(config.separators),
                    )
                )
        return self._build_chunks(texts, page=page)

    @staticmethod
    def _split_by_separators(text: str, separators: list[str]) -> list[str]:
        parts = [text]
        for separator in separators:
            next_parts: list[str] = []
            for part in parts:
                for piece in part.split(separator):
                    normalized = piece.strip()
                    if normalized:
                        next_parts.append(normalized)
            parts = next_parts
        return parts

    def _split_page(self, document: ParsedDocument, config: ChunkingConfig) -> list[DataChunk]:
        max_size = config.max_chunk_size or self._settings.RAG_CHUNK_SIZE
        pieces: list[tuple[str, int | None]] = []
        for block in document.blocks:
            text = block.text.strip()
            if not text:
                continue
            if len(text) <= max_size:
                pieces.append((text, block.page))
                continue
            for part in self._recursive_split(
                text,
                chunk_size=max_size,
                chunk_overlap=0,
                separators=list(_PARAGRAPH_SEPARATORS),
            ):
                pieces.append((part, block.page))
        return self._build_chunks_from_pairs(pieces)

    @staticmethod
    def _recursive_split(
        text: str,
        *,
        chunk_size: int,
        chunk_overlap: int,
        separators: list[str],
    ) -> list[str]:
        stripped = text.strip()
        if not stripped:
            return []
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=separators,
        )
        return splitter.split_text(stripped)

    @staticmethod
    def _first_block_page(blocks: list[ParsedBlock]) -> int | None:
        if not blocks:
            return None
        return blocks[0].page

    @staticmethod
    def _build_chunks(texts: list[str], *, page: int | None) -> list[DataChunk]:
        return ChunkingService._build_chunks_from_pairs([(text, page) for text in texts])

    @staticmethod
    def _build_chunks_from_pairs(pieces: list[tuple[str, int | None]]) -> list[DataChunk]:
        chunks: list[DataChunk] = []
        index = 0
        for text, page in pieces:
            normalized = text.strip()
            if not normalized:
                continue
            chunks.append(DataChunk(text=normalized, page=page, chunk_index=index))
            index += 1
        return chunks
