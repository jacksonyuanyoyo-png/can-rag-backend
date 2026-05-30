from __future__ import annotations

from app.domain.model_catalog import openai_chat_model_catalog, openai_embedding_model_catalog


def test_openai_catalog_ids_are_unique() -> None:
    entries = openai_chat_model_catalog()
    ids = [entry.id for entry in entries]
    assert len(ids) == len(set(ids))


def test_openai_embedding_catalog_ids_are_unique() -> None:
    entries = openai_embedding_model_catalog()
    ids = [entry.id for entry in entries]
    assert len(ids) == len(set(ids))
    assert "text-embedding-3-small" in ids
    assert "gpt-4o" not in ids
