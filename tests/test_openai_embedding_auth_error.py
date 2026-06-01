from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.services.rag.openai_embedding_service import OpenAIEmbeddingError, OpenAIEmbeddingService


def test_openai_embedding_401_raises_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": "Incorrect API key provided",
                    "type": "invalid_request_error",
                }
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    service = OpenAIEmbeddingService(
        settings=Settings(OPENAI_API_KEY="sk-invalid"),
        model="text-embedding-3-small",
        dimensions=1536,
    )

    with pytest.raises(OpenAIEmbeddingError) as exc_info:
        service.embed_many(["hello"])

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "openai_auth_failed"
    assert "OPENAI_API_KEY" in str(exc_info.value)
    assert "RAG_EMBEDDING_BACKEND=hash" in str(exc_info.value)
