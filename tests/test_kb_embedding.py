from __future__ import annotations

from app.core.config import Settings
from app.domain.knowledge_base import KnowledgeBaseMetadata
from app.services.rag.kb_embedding import resolve_kb_embedding_config


def test_resolve_kb_embedding_uses_hash_without_api_key() -> None:
    metadata = KnowledgeBaseMetadata(name="demo", description="")
    metadata.backend_refs["embedding_model_id"] = "text-embedding-3-large"
    config = resolve_kb_embedding_config(
        Settings(OPENAI_API_KEY="", RAG_EMBEDDING_DIMENSIONS=1536),
        metadata,
    )

    assert config.backend == "hash"
    assert config.model_id == "text-embedding-3-large"
    assert config.dimensions == 1536


def test_resolve_kb_embedding_uses_openai_when_configured() -> None:
    metadata = KnowledgeBaseMetadata(name="demo", description="")
    metadata.backend_refs["embedding_model_id"] = "text-embedding-3-small"
    config = resolve_kb_embedding_config(
        Settings(
            OPENAI_API_KEY="sk-test",
            RAG_EMBEDDING_DIMENSIONS=1536,
        ),
        metadata,
    )

    assert config.backend == "openai"
    assert config.model_id == "text-embedding-3-small"
    assert config.dimensions == 1536


def test_resolve_kb_embedding_forced_hash_backend() -> None:
    metadata = KnowledgeBaseMetadata(name="demo", description="")
    metadata.backend_refs["embedding_model_id"] = "text-embedding-3-large"
    config = resolve_kb_embedding_config(
        Settings(
            OPENAI_API_KEY="sk-invalid",
            RAG_EMBEDDING_DIMENSIONS=3072,
            RAG_EMBEDDING_BACKEND="hash",
        ),
        metadata,
    )

    assert config.backend == "hash"
    assert config.dimensions == 3072


def test_resolve_kb_embedding_forced_openai_backend() -> None:
    config = resolve_kb_embedding_config(
        Settings(
            OPENAI_API_KEY="sk-test",
            RAG_EMBEDDING_DIMENSIONS=256,
            RAG_EMBEDDING_BACKEND="openai",
        ),
        None,
    )

    assert config.backend == "openai"
    assert config.dimensions == 256


def test_resolve_kb_embedding_falls_back_when_dimensions_too_small_for_openai() -> None:
    metadata = KnowledgeBaseMetadata(name="demo", description="")
    metadata.backend_refs["embedding_model_id"] = "text-embedding-3-small"
    config = resolve_kb_embedding_config(
        Settings(
            OPENAI_API_KEY="sk-test",
            RAG_EMBEDDING_DIMENSIONS=256,
        ),
        metadata,
    )

    assert config.backend == "hash"
    assert config.dimensions == 256
