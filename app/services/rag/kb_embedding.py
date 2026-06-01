from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from app.core.config import Settings
from app.domain.knowledge_base import EMBEDDING_MODEL_ID_KEY, KnowledgeBaseMetadata
from app.domain.model_catalog import get_embedding_catalog_entry
from app.services.rag.embedder import HashEmbeddingService
from app.services.rag.openai_embedding_service import OpenAIEmbeddingService

logger = logging.getLogger(__name__)

EmbeddingBackend = Literal["openai", "hash"]
OPENAI_EMBEDDING_MIN_DIMENSIONS = 512
ADA002_DIMENSIONS = 1536


@dataclass(frozen=True, slots=True)
class KbEmbeddingConfig:
    model_id: str
    dimensions: int
    backend: EmbeddingBackend


class TextEmbedder(Protocol):
    def embed(self, text: str) -> list[float]: ...

    def embed_many(self, texts: list[str]) -> list[list[float]]: ...


def resolve_kb_embedding_config(
    settings: Settings,
    metadata: KnowledgeBaseMetadata | None,
) -> KbEmbeddingConfig:
    raw_model = None
    if metadata is not None:
        stored = metadata.backend_refs.get(EMBEDDING_MODEL_ID_KEY)
        if isinstance(stored, str) and stored.strip():
            raw_model = stored.strip()
    model_id = raw_model or settings.OPENAI_EMBEDDING_MODEL.strip() or "text-embedding-3-small"
    storage_dims = int(settings.RAG_EMBEDDING_DIMENSIONS)
    backend_mode = settings.RAG_EMBEDDING_BACKEND.strip().lower()

    if backend_mode == "hash":
        return KbEmbeddingConfig(model_id=model_id, dimensions=storage_dims, backend="hash")

    if not settings.OPENAI_API_KEY.strip():
        return KbEmbeddingConfig(model_id=model_id, dimensions=storage_dims, backend="hash")

    if backend_mode == "openai":
        return KbEmbeddingConfig(
            model_id=model_id,
            dimensions=storage_dims,
            backend="openai",
        )

    if model_id == "text-embedding-ada-002":
        if storage_dims != ADA002_DIMENSIONS:
            logger.warning(
                "embedding 模型 %s 需要 RAG_EMBEDDING_DIMENSIONS=%s，当前为 %s，回退 hash",
                model_id,
                ADA002_DIMENSIONS,
                storage_dims,
            )
            return KbEmbeddingConfig(model_id=model_id, dimensions=storage_dims, backend="hash")
        return KbEmbeddingConfig(
            model_id=model_id,
            dimensions=ADA002_DIMENSIONS,
            backend="openai",
        )

    if not model_id.startswith("text-embedding-3-"):
        logger.warning("未知 embedding 模型 %s，回退 hash", model_id)
        return KbEmbeddingConfig(model_id=model_id, dimensions=storage_dims, backend="hash")

    if storage_dims < OPENAI_EMBEDDING_MIN_DIMENSIONS:
        logger.warning(
            "RAG_EMBEDDING_DIMENSIONS=%s 低于 OpenAI 最小 %s，无法使用 %s，回退 hash",
            storage_dims,
            OPENAI_EMBEDDING_MIN_DIMENSIONS,
            model_id,
        )
        return KbEmbeddingConfig(model_id=model_id, dimensions=storage_dims, backend="hash")

    entry = get_embedding_catalog_entry(model_id)
    if entry is not None and storage_dims > entry.dimensions:
        logger.warning(
            "RAG_EMBEDDING_DIMENSIONS=%s 超过模型 %s 上限 %s，回退 hash",
            storage_dims,
            model_id,
            entry.dimensions,
        )
        return KbEmbeddingConfig(model_id=model_id, dimensions=storage_dims, backend="hash")

    return KbEmbeddingConfig(
        model_id=model_id,
        dimensions=storage_dims,
        backend="openai",
    )


class EmbedderFactory:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[tuple[str, int, EmbeddingBackend], TextEmbedder] = {}

    def get(self, config: KbEmbeddingConfig) -> TextEmbedder:
        key = (config.model_id, config.dimensions, config.backend)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if config.backend == "openai":
            embedder: TextEmbedder = OpenAIEmbeddingService(
                settings=self._settings,
                model=config.model_id,
                dimensions=config.dimensions,
            )
        else:
            embedder = HashEmbeddingService(dimensions=config.dimensions)
        self._cache[key] = embedder
        return embedder
