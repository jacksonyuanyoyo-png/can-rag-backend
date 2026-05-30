from __future__ import annotations

from pydantic import BaseModel, Field


class PresignFileItem(BaseModel):
    file_name: str = Field(min_length=1, alias="fileName")
    mime_type: str = Field(min_length=1, alias="mimeType")
    size_bytes: int = Field(gt=0, alias="sizeBytes")

    model_config = {"populate_by_name": True}


class PresignUploadRequest(BaseModel):
    knowledge_base_id: str = Field(min_length=1, alias="knowledgeBaseId")
    files: list[PresignFileItem] = Field(min_length=1)

    model_config = {"populate_by_name": True}


class CompleteUploadRequest(BaseModel):
    file_id: str = Field(min_length=1, alias="fileId")
    storage_key: str = Field(min_length=1, alias="storageKey")
    etag: str | None = None

    model_config = {"populate_by_name": True}
