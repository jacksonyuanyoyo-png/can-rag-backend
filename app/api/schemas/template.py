from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CreateTemplateRequest(BaseModel):
    name: str = Field(min_length=1)
    content: str = Field(min_length=1)
    snippet: str | None = None


class UpdateTemplateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    content: str | None = Field(default=None, min_length=1)
    snippet: str | None = None


class DeleteTemplateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    success: bool = True
