from __future__ import annotations

import pytest

from app.core.config import Settings
from app.domain.import_job import ChunkingConfig
from app.services.rag.chunking_service import ChunkingService, DataChunk, IndexChunk
from app.services.rag.parsing.base import ParsedBlock, ParsedDocument


def _sample_document() -> ParsedDocument:
    paragraph_one = "第一段内容。" * 30
    paragraph_two = "第二段内容。" * 30
    full_text = f"{paragraph_one}\n\n{paragraph_two}"
    return ParsedDocument(
        full_text=full_text,
        blocks=[
            ParsedBlock(page=1, text=paragraph_one, heading="标题一"),
            ParsedBlock(page=2, text=paragraph_two, heading="标题二"),
        ],
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(RAG_CHUNK_SIZE=80, RAG_CHUNK_OVERLAP=10)


@pytest.fixture
def service(settings: Settings) -> ChunkingService:
    return ChunkingService(settings=settings)


def test_default_strategy_produces_multiple_sequential_chunks(
    service: ChunkingService,
) -> None:
    document = _sample_document()
    config = ChunkingConfig(strategy="default", max_chunk_size=80, overlap=10)

    chunks = service.split(document, config)

    assert len(chunks) > 1
    assert all(isinstance(chunk, DataChunk) for chunk in chunks)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert chunks[0].page == 1


def test_length_mode_respects_chunk_size_and_overlap(service: ChunkingService) -> None:
    document = ParsedDocument(
        full_text="abcdefghij" * 50,
        blocks=[],
    )
    config = ChunkingConfig(
        strategy="custom",
        mode="length",
        chunk_size=100,
        overlap=20,
    )

    chunks = service.split(document, config)

    assert len(chunks) >= 4
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(len(chunk.text) <= 100 for chunk in chunks)


def test_separator_mode_splits_on_configured_delimiter(service: ChunkingService) -> None:
    document = ParsedDocument(
        full_text="alpha|||beta|||gamma",
        blocks=[],
    )
    config = ChunkingConfig(
        strategy="custom",
        mode="separator",
        separators=["|||"],
    )

    chunks = service.split(document, config)

    assert len(chunks) == 3
    assert [chunk.text for chunk in chunks] == ["alpha", "beta", "gamma"]


def test_page_mode_one_chunk_per_block_preserves_page(service: ChunkingService) -> None:
    document = ParsedDocument(
        full_text="ignored for page strategy",
        blocks=[
            ParsedBlock(page=1, text="第一页内容"),
            ParsedBlock(page=2, text="第二页内容"),
            ParsedBlock(page=3, text="第三页内容"),
        ],
    )
    config = ChunkingConfig(strategy="page")

    chunks = service.split(document, config)

    assert len(chunks) == 3
    assert [chunk.page for chunk in chunks] == [1, 2, 3]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]


def test_whole_strategy_raises_value_error(service: ChunkingService) -> None:
    document = _sample_document()
    config = ChunkingConfig(strategy="whole")

    with pytest.raises(ValueError, match="whole"):
        service.split(document, config)


def _index_config(**overrides: object) -> ChunkingConfig:
    base = {
        "strategy": "default",
        "index_size": 512,
        "meta_filename": False,
        "meta_headings": False,
    }
    base.update(overrides)
    return ChunkingConfig(**base)


def test_short_data_produces_one_index_per_data_chunk(service: ChunkingService) -> None:
    config = _index_config(index_size=512)
    data_chunks = [
        DataChunk(text="短文本一", page=1, chunk_index=0),
        DataChunk(text="短文本二", page=2, chunk_index=1),
    ]

    indexes = service.build_indexes(data_chunks, config)

    assert len(indexes) == len(data_chunks)
    assert all(isinstance(index, IndexChunk) for index in indexes)
    assert [index.data_chunk_index for index in indexes] == [0, 1]
    assert all(index.index_in_data == 0 for index in indexes)


def test_long_data_splits_into_multiple_indexes_with_consecutive_index_in_data(
    service: ChunkingService,
) -> None:
    config = _index_config(index_size=256)
    long_text = "长" * 1000
    data_chunks = [DataChunk(text=long_text, page=3, chunk_index=0)]

    indexes = service.build_indexes(data_chunks, config)

    assert len(indexes) > 1
    assert [index.index_in_data for index in indexes] == list(range(len(indexes)))
    assert all(index.data_chunk_index == 0 for index in indexes)
    assert "".join(index.raw_text for index in indexes) == long_text


def test_meta_filename_prefix_in_text_not_in_raw_text(service: ChunkingService) -> None:
    config = _index_config(index_size=512, meta_filename=True)
    data_chunks = [DataChunk(text="正文内容", page=1, chunk_index=0)]
    file_name = "报告.pdf"

    indexes = service.build_indexes(data_chunks, config, file_name=file_name)

    assert len(indexes) == 1
    assert indexes[0].text.startswith(f"文件：{file_name}\n")
    assert indexes[0].raw_text == "正文内容"
    assert "文件：" not in indexes[0].raw_text


def test_index_citation_includes_file_name_page_and_data_id(
    service: ChunkingService,
) -> None:
    config = _index_config(index_size=512)
    data_chunks = [DataChunk(text="引用测试", page=7, chunk_index=2)]
    file_name = "手册.docx"

    indexes = service.build_indexes(
        data_chunks,
        config,
        file_name=file_name,
        data_id_of=lambda chunk: f"data-{chunk.chunk_index}",
    )

    assert len(indexes) == 1
    assert indexes[0].citation == {
        "file_name": file_name,
        "page": 7,
        "data_id": "data-2",
    }
