from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CreateFolderRequest(BaseModel):
    name: str = Field(min_length=1)


class UpdateFolderRequest(BaseModel):
    name: str = Field(min_length=1)


class DeleteFolderResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    success: bool = True
