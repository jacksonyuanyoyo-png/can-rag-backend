from __future__ import annotations

from typing import Protocol

from app.domain.knowledge_base import BackendType, SearchHit


class KnowledgeBackend(Protocol):
    """统一后端协议：OpenAI、FastGPT、本地向量库都应实现这一层。"""

    backend_type: BackendType

    async def search(self, *, knowledge_base: str, query: str, top_k: int) -> list[SearchHit]:
        ...

    async def chat(
        self,
        *,
        knowledge_base: str,
        query: str,
        history: list[dict],
        top_k: int,
    ) -> dict:
        ...
