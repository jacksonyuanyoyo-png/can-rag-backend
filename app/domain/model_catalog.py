from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 历史占位模型：后端未接入，同步时标记为 inactive
LEGACY_PLACEHOLDER_MODEL_IDS: frozenset[str] = frozenset(
    {
        "claude-sonnet-4",
        "gemini",
        "assistant",
    }
)


@dataclass(frozen=True, slots=True)
class ModelCatalogEntry:
    """与 OpenAI Chat Completions 对齐、可被对话接口直接调用的模型。"""

    id: str
    code: str
    display_name: str
    provider: str = "openai"
    icon: str = "/models/openai.svg"
    status: str = "active"
    visibility: str = "system"

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.display_name,
            "icon": self.icon,
            "provider": self.provider,
            "status": self.status,
            "visibility": self.visibility,
        }


def openai_chat_model_catalog() -> list[ModelCatalogEntry]:
    """当前网关实际支持的 OpenAI 对话模型（与 OpenAIChatService.resolve_model 一致）。"""
    return [
        ModelCatalogEntry(
            id="gpt-4o-mini",
            code="gpt-4o-mini",
            display_name="GPT-4o mini",
        ),
        ModelCatalogEntry(
            id="gpt-4o",
            code="gpt-4o",
            display_name="GPT-4o",
        ),
        ModelCatalogEntry(
            id="gpt-5",
            code="gpt-5",
            display_name="GPT-5",
        ),
        ModelCatalogEntry(
            id="gpt-5-mini",
            code="gpt-5-mini",
            display_name="GPT-5 mini",
        ),
        ModelCatalogEntry(
            id="o4-mini",
            code="o4-mini",
            display_name="o4-mini",
        ),
        ModelCatalogEntry(
            id="o3-mini",
            code="o3-mini",
            display_name="o3-mini",
        ),
    ]


@dataclass(frozen=True, slots=True)
class EmbeddingModelCatalogEntry:
    """OpenAI Embeddings API 支持的向量模型（用于知识库创建时的 embeddingModelId）。"""

    id: str
    code: str
    display_name: str
    dimensions: int
    max_input_tokens: int
    provider: str = "openai"
    icon: str = "/models/openai.svg"
    status: str = "active"
    description: str = ""

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.display_name,
            "provider": self.provider,
            "icon": self.icon,
            "status": self.status,
            "dimensions": self.dimensions,
            "maxInputTokens": self.max_input_tokens,
            "description": self.description,
        }


def openai_embedding_model_catalog() -> list[EmbeddingModelCatalogEntry]:
    """与 OpenAI Embeddings API 对齐的向量模型目录。"""
    return [
        EmbeddingModelCatalogEntry(
            id="text-embedding-3-small",
            code="text-embedding-3-small",
            display_name="text-embedding-3-small",
            dimensions=1536,
            max_input_tokens=8191,
            description="性价比优先，适合大多数 RAG 场景",
        ),
        EmbeddingModelCatalogEntry(
            id="text-embedding-3-large",
            code="text-embedding-3-large",
            display_name="text-embedding-3-large",
            dimensions=3072,
            max_input_tokens=8191,
            description="召回质量更高，维度更大（网关默认推荐）",
        ),
        EmbeddingModelCatalogEntry(
            id="text-embedding-ada-002",
            code="text-embedding-ada-002",
            display_name="text-embedding-ada-002",
            dimensions=1536,
            max_input_tokens=8191,
            status="deprecated",
            description="旧版模型，仅兼容历史知识库",
        ),
    ]


def embedding_model_ids() -> frozenset[str]:
    return frozenset(entry.id for entry in openai_embedding_model_catalog())


def is_valid_embedding_model_id(model_id: str) -> bool:
    return model_id.strip() in embedding_model_ids()


def get_embedding_catalog_entry(model_id: str) -> EmbeddingModelCatalogEntry | None:
    needle = model_id.strip()
    for entry in openai_embedding_model_catalog():
        if entry.id == needle:
            return entry
    return None


def default_embedding_model_id(settings_default: str) -> str:
    candidate = settings_default.strip()
    if candidate and is_valid_embedding_model_id(candidate):
        return candidate
    return "text-embedding-3-small"
