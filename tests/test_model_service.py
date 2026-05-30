from __future__ import annotations

from app.core.config import Settings
from app.services.model_service import ModelService


def test_model_service_falls_back_to_openai_catalog_without_repository() -> None:
    service = ModelService(settings=Settings())
    models = service.list_models()
    ids = {model.id for model in models}

    assert "gpt-4o-mini" in ids
    assert "gpt-5" in ids
    assert "claude-sonnet-4" not in ids
    assert all(model.provider == "openai" for model in models)
    assert models[0].icon.startswith("/models/")


def test_model_service_lists_openai_embedding_models() -> None:
    service = ModelService(settings=Settings())
    models = service.list_embedding_models()
    ids = {model.id for model in models}

    assert "text-embedding-3-small" in ids
    assert "text-embedding-3-large" in ids
    assert "gpt-4o" not in ids
    first = next(model for model in models if model.id == "text-embedding-3-small")
    assert first.dimensions == 1536
    assert first.max_input_tokens == 8191
