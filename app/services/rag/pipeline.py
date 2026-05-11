from __future__ import annotations

from uuid import uuid4

from app.core.config import Settings
from app.domain.knowledge_base import DocumentMetadata, SearchHit
from app.services.rag.chunker import TextChunker
from app.services.rag.embedder import HashEmbeddingService
from app.services.rag.reranker import SimpleReranker
from app.services.rag.vector_store import JsonVectorStore, VectorRecord


class RagPipeline:
    """本地 RAG 管线：chunk -> embed -> vector store -> rerank -> citation。"""

    def __init__(self, settings: Settings) -> None:
        self._chunker = TextChunker(
            chunk_size=settings.RAG_CHUNK_SIZE,
            overlap=settings.RAG_CHUNK_OVERLAP,
        )
        self._embedder = HashEmbeddingService(dimensions=settings.RAG_EMBEDDING_DIMENSIONS)
        self._vector_store = JsonVectorStore(settings.vector_store_path_resolved)
        self._reranker = SimpleReranker()

    def index_document(
        self,
        *,
        knowledge_base: str,
        file_name: str,
        text: str,
        content_type: str | None = None,
    ) -> DocumentMetadata:
        document = DocumentMetadata(
            document_id=str(uuid4()),
            file_name=file_name,
            content_type=content_type,
        )
        records = [
            VectorRecord(
                knowledge_base=knowledge_base,
                document_id=document.document_id,
                file_name=file_name,
                chunk_id=chunk.chunk_id,
                text=chunk.text,
                embedding=self._embedder.embed(chunk.text),
                citation={
                    "file_name": file_name,
                    "chunk_id": chunk.chunk_id,
                    "start": chunk.start,
                    "end": chunk.end,
                },
            )
            for chunk in self._chunker.split(text)
        ]
        self._vector_store.upsert_document(knowledge_base, records)
        return document

    def delete_document(self, knowledge_base: str, document_id: str) -> None:
        self._vector_store.delete_document(knowledge_base, document_id)

    def delete_knowledge_base(self, knowledge_base: str) -> None:
        self._vector_store.delete_knowledge_base(knowledge_base)

    def search(self, *, knowledge_base: str, query: str, top_k: int) -> list[SearchHit]:
        query_embedding = self._embedder.embed(query)
        hits = self._vector_store.search(knowledge_base, query_embedding, top_k=max(top_k * 3, top_k))
        reranked = self._reranker.rerank(query, hits, top_k)
        return [
            SearchHit(
                document_id=record.document_id,
                file_name=record.file_name,
                chunk_id=record.chunk_id,
                text=record.text,
                score=score,
                citation=record.citation,
            )
            for record, score in reranked
        ]
