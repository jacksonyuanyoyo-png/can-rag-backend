from __future__ import annotations

import httpx

from app.core.config import Settings


class OpenAIEmbeddingError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _raise_for_response(response: httpx.Response) -> None:
    status = response.status_code
    body_preview = response.text[:300]
    if status == 401:
        raise OpenAIEmbeddingError(
            "OpenAI API Key 无效或已过期（401）。请在运行后端的 .env 中设置有效的 "
            "OPENAI_API_KEY（https://platform.openai.com/account/api-keys），"
            "并确认 OPENAI_BASE_URL 与 Key 所属平台一致；本地开发可设 "
            "RAG_EMBEDDING_BACKEND=hash 跳过 OpenAI 向量化。",
            status_code=status,
            code="openai_auth_failed",
        )
    if status == 403:
        raise OpenAIEmbeddingError(
            "OpenAI Embeddings 访问被拒绝（403），请检查账号权限与模型可用性。",
            status_code=status,
            code="openai_forbidden",
        )
    if status == 429:
        raise OpenAIEmbeddingError(
            "OpenAI Embeddings 请求过于频繁（429），请稍后重试。",
            status_code=status,
            code="openai_rate_limited",
        )
    raise OpenAIEmbeddingError(
        f"OpenAI embeddings 失败: status={status} body={body_preview}",
        status_code=status,
        code="openai_api_error",
    )


class OpenAIEmbeddingService:
    """调用 OpenAI Embeddings API，供知识库导入与检索使用。"""

    def __init__(
        self,
        *,
        settings: Settings,
        model: str,
        dimensions: int,
        timeout_seconds: float | None = None,
    ) -> None:
        if not settings.OPENAI_API_KEY.strip():
            raise ValueError("OpenAIEmbeddingService 需要 OPENAI_API_KEY")
        self._api_key = settings.OPENAI_API_KEY.strip()
        self._base_url = settings.OPENAI_BASE_URL.rstrip("/")
        self._model = model.strip()
        self._dimensions = dimensions
        self._timeout = timeout_seconds or settings.HTTP_TIMEOUT_SECONDS
        self._supports_dimensions = self._model.startswith("text-embedding-3-")

    def embed(self, text: str) -> list[float]:
        vectors = self.embed_many([text])
        if not vectors:
            raise OpenAIEmbeddingError("OpenAI embeddings 返回空结果")
        return vectors[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        cleaned = [text.strip() for text in texts if text.strip()]
        if not cleaned:
            return []

        payload: dict[str, object] = {
            "model": self._model,
            "input": cleaned,
        }
        if self._supports_dimensions:
            payload["dimensions"] = self._dimensions

        response = httpx.post(
            f"{self._base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self._timeout,
        )
        if response.status_code >= 400:
            _raise_for_response(response)

        body = response.json()
        rows = body.get("data")
        if not isinstance(rows, list):
            raise OpenAIEmbeddingError("OpenAI embeddings 响应缺少 data")

        ordered = sorted(rows, key=lambda item: int(item.get("index", 0)))
        vectors: list[list[float]] = []
        for row in ordered:
            embedding = row.get("embedding")
            if not isinstance(embedding, list):
                raise OpenAIEmbeddingError("OpenAI embeddings 响应格式无效")
            vector = [float(value) for value in embedding]
            if len(vector) != self._dimensions:
                raise OpenAIEmbeddingError(
                    f"向量维度不匹配: expected={self._dimensions}, actual={len(vector)}"
                )
            vectors.append(vector)
        return vectors
