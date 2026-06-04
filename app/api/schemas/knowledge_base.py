from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ResourceType = Literal["personal", "team"]


class KnowledgeBaseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=400)
    embedding_model_id: str | None = Field(default=None, alias="embeddingModelId")

    model_config = {"populate_by_name": True}


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    description: str
    file_count: int = Field(alias="fileCount")
    resource_type: ResourceType = Field(alias="resourceType")
    updated_at: str = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class DeleteKnowledgeBaseResponse(BaseModel):
    success: bool = True


class KnowledgeBaseUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=400)
    resource_type: ResourceType | None = Field(default=None, alias="resourceType")

    model_config = {"populate_by_name": True}


class KnowledgeBaseFileDetailResponse(BaseModel):
    id: str
    name: str
    format: str
    status: str
    char_count: int = Field(alias="charCount")
    uploaded_at: str = Field(alias="uploadedAt")
    tags: list[str] | None = None
    mime_type: str | None = Field(default=None, alias="mimeType")
    size_bytes: int | None = Field(default=None, alias="sizeBytes")
    error_message: str | None = Field(default=None, alias="errorMessage")
    storage_key: str | None = Field(default=None, alias="storageKey")
    source_file_url: str = Field(alias="sourceFileUrl")

    model_config = {"populate_by_name": True}


class DeleteFileResponse(BaseModel):
    success: bool = True


class BatchDeleteFilesRequest(BaseModel):
    file_ids: list[str] = Field(alias="fileIds")

    model_config = {"populate_by_name": True}


class BatchDeleteFileFailureItem(BaseModel):
    file_id: str = Field(alias="fileId")
    code: str
    message: str

    model_config = {"populate_by_name": True}


class BatchDeleteFilesResponse(BaseModel):
    succeeded: list[str]
    failed: list[BatchDeleteFileFailureItem]

    model_config = {"populate_by_name": True}


class HitTestFilters(BaseModel):
    file_ids: list[str] = Field(default_factory=list, alias="fileIds")

    model_config = {"populate_by_name": True}


class HitTestRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, alias="topK")
    filters: HitTestFilters | None = None

    model_config = {"populate_by_name": True}


class HitTestResultItem(BaseModel):
    file_id: str = Field(alias="fileId")
    chunk_id: str = Field(alias="chunkId")
    score: float
    snippet: str
    page: int | None = None

    model_config = {"populate_by_name": True}


class HitTestResponse(BaseModel):
    results: list[HitTestResultItem]
    latency_ms: int = Field(alias="latencyMs")

    model_config = {"populate_by_name": True}


class FileChunkIndexItem(BaseModel):
    index_id: str = Field(alias="indexId")
    text: str

    model_config = {"populate_by_name": True}


class FileChunkItemResponse(BaseModel):
    data_id: str = Field(alias="dataId")
    text: str
    char_count: int = Field(alias="charCount")
    page: int | None = None
    chunk_index: int = Field(alias="chunkIndex")
    citation: dict[str, object]
    indexes: list[FileChunkIndexItem]

    model_config = {"populate_by_name": True}


class FileChunkContextItemResponse(BaseModel):
    data_id: str = Field(alias="dataId")
    chunk_index: int = Field(alias="chunkIndex")
    page: int | None = None
    text: str

    model_config = {"populate_by_name": True}


class FileChunkContextBlockResponse(BaseModel):
    before: list[FileChunkContextItemResponse]
    after: list[FileChunkContextItemResponse]

    model_config = {"populate_by_name": True}


class FileChunkWithContextResponse(BaseModel):
    target: FileChunkItemResponse
    context: FileChunkContextBlockResponse

    model_config = {"populate_by_name": True}
