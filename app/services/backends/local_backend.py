from __future__ import annotations

from app.domain.knowledge_base import BackendType, SearchHit
from app.services.rag.pipeline import RagPipeline


class LocalKnowledgeBackend:
    backend_type = BackendType.LOCAL

    def __init__(self, rag_pipeline: RagPipeline) -> None:
        self._rag_pipeline = rag_pipeline

    async def search(self, *, knowledge_base: str, query: str, top_k: int) -> list[SearchHit]:
        return self._rag_pipeline.search(knowledge_base=knowledge_base, query=query, top_k=top_k)

    async def chat(
        self,
        *,
        knowledge_base: str,
        query: str,
        history: list[dict],
        top_k: int,
    ) -> dict:
        del history
        hits = await self.search(knowledge_base=knowledge_base, query=query, top_k=top_k)
        return {
            "answer": "\n\n".join(hit.text for hit in hits),
            "citations": [hit.citation for hit in hits],
        }
