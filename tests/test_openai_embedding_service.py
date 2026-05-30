from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.services.rag.openai_embedding_service import OpenAIEmbeddingService


def test_openai_embedding_service_embed_many(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    service = OpenAIEmbeddingService(
        settings=Settings(OPENAI_API_KEY="sk-test", RAG_EMBEDDING_DIMENSIONS=3),
        model="text-embedding-3-small",
        dimensions=3,
    )

    vectors = service.embed_many(["hello", "world"])

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "text-embedding-3-small"
    assert payload["dimensions"] == 3
