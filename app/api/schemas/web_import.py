from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.api.schemas.import_job import ChunkingOptions, ParsingOptions


class CreateWebImportRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    url: HttpUrl
    auto_import: bool = Field(default=True, alias="autoImport")
    use_browser_fallback: bool | None = Field(default=None, alias="useBrowserFallback")
    chunk_strategy: str = Field(default="default", alias="chunkStrategy")
    chunking: ChunkingOptions | None = None
    parsing: ParsingOptions | None = None
