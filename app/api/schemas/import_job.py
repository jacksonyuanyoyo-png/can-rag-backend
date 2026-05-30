from __future__ import annotations

from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

INVALID_OPTIONS_CODE = "IMPORT_INVALID_OPTIONS"


class ChunkStrategy(StrEnum):
    DEFAULT = "default"
    CUSTOM = "custom"
    WHOLE = "whole"
    PAGE = "page"


class CustomChunkMode(StrEnum):
    PARAGRAPH = "paragraph"
    LENGTH = "length"
    SEPARATOR = "separator"


class IndexSize(IntEnum):
    SIZE_256 = 256
    SIZE_512 = 512
    SIZE_1024 = 1024


class ImportJobMetadataOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    include_file_name: bool = Field(default=True, alias="includeFileName")
    include_headings: bool = Field(default=False, alias="includeHeadings")


class CustomChunkConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mode: CustomChunkMode


class ParagraphChunkConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    use_model: bool = Field(default=False, alias="useModel")
    max_depth: int = Field(alias="maxDepth", gt=0)


class LengthChunkConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chunk_size: int = Field(alias="chunkSize", gt=0)
    overlap: int = Field(alias="overlap", ge=0)
    max_chunk_size: int = Field(alias="maxChunkSize", gt=0)

    @model_validator(mode="after")
    def _validate_bounds(self) -> LengthChunkConfig:
        if self.overlap >= self.chunk_size:
            raise ValueError(
                f"{INVALID_OPTIONS_CODE}: length.overlap 必须小于 length.chunkSize"
            )
        if self.chunk_size > self.max_chunk_size:
            raise ValueError(
                f"{INVALID_OPTIONS_CODE}: length.chunkSize 必须不大于 length.maxChunkSize"
            )
        return self


class SeparatorChunkConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    separators: list[str] = Field(min_length=1)


class ChunkingOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    strategy: ChunkStrategy = Field(default=ChunkStrategy.DEFAULT)
    custom: CustomChunkConfig | None = None
    paragraph: ParagraphChunkConfig | None = None
    length: LengthChunkConfig | None = None
    separator: SeparatorChunkConfig | None = None
    index_size: IndexSize | None = Field(default=None, alias="indexSize")
    metadata: ImportJobMetadataOptions | None = None

    @model_validator(mode="after")
    def _validate_chunking(self) -> ChunkingOptions:
        if self.strategy == ChunkStrategy.WHOLE:
            raise ValueError(
                f"{INVALID_OPTIONS_CODE}: strategy=whole 暂未开放"
            )

        if self.strategy == ChunkStrategy.CUSTOM:
            if self.custom is None:
                raise ValueError(
                    f"{INVALID_OPTIONS_CODE}: strategy=custom 时必须提供 custom.mode"
                )
            mode = self.custom.mode
            if mode == CustomChunkMode.PARAGRAPH and self.paragraph is None:
                raise ValueError(
                    f"{INVALID_OPTIONS_CODE}: custom.mode=paragraph 时必须提供 paragraph 配置"
                )
            if mode == CustomChunkMode.LENGTH and self.length is None:
                raise ValueError(
                    f"{INVALID_OPTIONS_CODE}: custom.mode=length 时必须提供 length 配置"
                )
            if mode == CustomChunkMode.SEPARATOR and self.separator is None:
                raise ValueError(
                    f"{INVALID_OPTIONS_CODE}: custom.mode=separator 时必须提供 separator 配置"
                )

        if self.index_size is not None and self.length is not None:
            if int(self.index_size) > self.length.max_chunk_size:
                raise ValueError(
                    f"{INVALID_OPTIONS_CODE}: indexSize 必须不大于 length.maxChunkSize"
                )

        return self


class CreateImportJobRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_ids: list[str] = Field(min_length=1, alias="fileIds")
    chunk_strategy: str = Field(default="default", alias="chunkStrategy")
    chunking: ChunkingOptions | None = None
    metadata: ImportJobMetadataOptions = Field(default_factory=ImportJobMetadataOptions)

    @model_validator(mode="after")
    def _apply_chunking_precedence(self) -> CreateImportJobRequest:
        if self.chunking is not None:
            self.chunk_strategy = self.chunking.strategy.value
            if self.chunking.metadata is not None:
                self.metadata = self.chunking.metadata
        return self


class ImportJobRetryOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chunk_strategy: str | None = Field(default=None, alias="chunkStrategy")
    chunk_size: int | None = Field(default=None, alias="chunkSize")
    chunk_overlap: int | None = Field(default=None, alias="chunkOverlap")
    include_file_name: bool | None = Field(default=None, alias="includeFileName")
    include_headings: bool | None = Field(default=None, alias="includeHeadings")


class RetryImportJobRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    options: ImportJobRetryOptions | None = None
    chunking: ChunkingOptions | None = None

    @model_validator(mode="after")
    def _apply_chunking_precedence(self) -> RetryImportJobRequest:
        if self.chunking is not None:
            options = self.options or ImportJobRetryOptions()
            options.chunk_strategy = self.chunking.strategy.value
            if self.chunking.metadata is not None:
                options.include_file_name = self.chunking.metadata.include_file_name
                options.include_headings = self.chunking.metadata.include_headings
            if self.chunking.length is not None:
                options.chunk_size = self.chunking.length.chunk_size
                options.chunk_overlap = self.chunking.length.overlap
            self.options = options
        return self
