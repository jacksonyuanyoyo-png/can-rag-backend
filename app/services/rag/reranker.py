from __future__ import annotations

from app.services.rag.vector_store import VectorRecord


class SimpleReranker:
    """轻量 rerank，占位规则：向量分优先，命中 query 文本时小幅加权。"""

    def rerank(
        self,
        query: str,
        hits: list[tuple[VectorRecord, float]],
        top_k: int,
    ) -> list[tuple[VectorRecord, float]]:
        query_text = query.strip().lower()
        reranked: list[tuple[VectorRecord, float]] = []
        for record, score in hits:
            bonus = 0.05 if query_text and query_text in record.text.lower() else 0.0
            reranked.append((record, score + bonus))
        reranked.sort(key=lambda item: item[1], reverse=True)
        return reranked[:top_k]
