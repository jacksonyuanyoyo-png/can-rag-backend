from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.model_catalog import MODEL_TAG_EMBEDDING, MODEL_TAG_INFERENCE


class ModelItem(BaseModel):
    """统一模型目录项：对话/推理与 embedding 共用，通过 tag 区分。"""

    id: str
    name: str
    icon: str
    tag: str
    provider: str | None = None
    status: str = "active"
    visibility: str | None = None
    dimensions: int | None = None
    max_input_tokens: int | None = Field(default=None, alias="maxInputTokens")
    description: str | None = None

    model_config = {"populate_by_name": True}


class EmbeddingModelItem(BaseModel):
    """兼容 /v1/embedding-models；字段与 ModelItem（tag=embedding）一致。"""

    id: str
    name: str
    icon: str
    tag: str = MODEL_TAG_EMBEDDING
    provider: str = "openai"
    status: str = "active"
    dimensions: int
    max_input_tokens: int = Field(alias="maxInputTokens")
    description: str = ""

    model_config = {"populate_by_name": True}

    def to_model_item(self) -> ModelItem:
        return ModelItem(
            id=self.id,
            name=self.name,
            icon=self.icon,
            tag=MODEL_TAG_EMBEDDING,
            provider=self.provider,
            status=self.status,
            dimensions=self.dimensions,
            max_input_tokens=self.max_input_tokens,
            description=self.description or None,
        )


def inference_model_item(
    *,
    id: str,
    name: str,
    icon: str,
    provider: str | None = "openai",
    status: str = "active",
    visibility: str | None = "system",
) -> ModelItem:
    return ModelItem(
        id=id,
        name=name,
        icon=icon,
        tag=MODEL_TAG_INFERENCE,
        provider=provider,
        status=status,
        visibility=visibility,
    )
