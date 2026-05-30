from __future__ import annotations

from pydantic import BaseModel, Field


class ModelItem(BaseModel):
    id: str
    name: str
    icon: str
    provider: str | None = None
    status: str = "active"
    visibility: str = "system"


class EmbeddingModelItem(BaseModel):
    id: str
    name: str
    icon: str
    provider: str = "openai"
    status: str = "active"
    dimensions: int
    max_input_tokens: int = Field(alias="maxInputTokens")
    description: str = ""

    model_config = {"populate_by_name": True}
