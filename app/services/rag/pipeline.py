from __future__ import annotations

import json
import re
from uuid import uuid4

from app.core.config import Settings
from app.domain.import_job import ChunkingConfig
from app.domain.knowledge_base import DocumentMetadata, SearchHit
from app.services.rag.chunker import TextChunker
from app.services.rag.chunking_service import ChunkingService
from app.services.rag.embedder import HashEmbeddingService
from app.services.rag.kb_embedding import EmbedderFactory, KbEmbeddingConfig, resolve_kb_embedding_config
from app.services.rag.parsing.base import ParsedDocument
from app.services.rag.parsing.image_store import ImageStore
from app.services.rag.reranker import SimpleReranker
from app.services.rag.vlm_service import VlmService
from app.services.rag.vector_store import (
    DataRecord,
    IndexRecord,
    JsonVectorStore,
    PgVectorStore,
    VectorRecord,
)

try:
    from langsmith import traceable
except Exception:  # pragma: no cover - optional dependency fallback
    def traceable(*_args, **_kwargs):  # type: ignore[no-redef]
        def _decorator(func):
            return func

        return _decorator


def _guess_mime(storage_key: str) -> str:
    suffix = storage_key.rsplit(".", 1)[-1].lower() if "." in storage_key else ""
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(suffix, "image/png")


class _HashRagPipeline:
    """Hash embedding RAG 管线：chunk -> embed -> vector store -> rerank -> citation。"""

    def __init__(
        self,
        settings: Settings,
        vector_store: JsonVectorStore | PgVectorStore,
        *,
        vlm_service: VlmService | None = None,
    ) -> None:
        self._settings = settings
        self._chunker = TextChunker(
            chunk_size=settings.RAG_CHUNK_SIZE,
            overlap=settings.RAG_CHUNK_OVERLAP,
        )
        self._default_embedding_config = resolve_kb_embedding_config(settings, None)
        self._embedder_factory = EmbedderFactory(settings)
        self._legacy_embedder = HashEmbeddingService(dimensions=settings.RAG_EMBEDDING_DIMENSIONS)
        self._vector_store = vector_store
        self._reranker = SimpleReranker()
        self._vlm = vlm_service if vlm_service is not None else VlmService(settings)
        self._image_store = ImageStore(settings.upload_root_resolved)

    @traceable(name="local_rag_index_document", run_type="chain")
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
                embedding=self._legacy_embedder.embed(chunk.text),
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

    @traceable(name="local_rag_search", run_type="retriever")
    def count_chunks(self, knowledge_base: str) -> int:
        return self._vector_store.count_chunks(knowledge_base)

    def count_chunks_by_document(self, knowledge_base: str) -> dict[str, int]:
        return self._vector_store.count_chunks_by_document(knowledge_base)

    def search(self, *, knowledge_base: str, query: str, top_k: int) -> list[SearchHit]:
        query_embedding = self._legacy_embedder.embed(query)
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

    def index_data(
        self,
        *,
        knowledge_base: str,
        file_id: str,
        document: ParsedDocument,
        config: ChunkingConfig,
        file_name: str,
        embedding_config: KbEmbeddingConfig | None = None,
    ) -> dict[str, int]:
        embedder = self._embedder_factory.get(
            embedding_config or self._default_embedding_config
        )
        data_chunks, index_chunks = ChunkingService().split_and_index(
            document,
            config,
            file_name=file_name,
            data_id_of=lambda chunk: f"d{chunk.chunk_index:06d}",
        )
        data_records = [
            DataRecord(
                knowledge_base=knowledge_base,
                file_id=file_id,
                data_id=f"d{chunk.chunk_index:06d}",
                text=chunk.text,
                page=chunk.page,
                chunk_index=chunk.chunk_index,
                citation={
                    "file_name": file_name,
                    "page": chunk.page,
                    "data_id": f"d{chunk.chunk_index:06d}",
                },
            )
            for chunk in data_chunks
        ]
        index_texts = [index_chunk.text for index_chunk in index_chunks]
        index_vectors = embedder.embed_many(index_texts) if index_texts else []
        index_records = [
            IndexRecord(
                knowledge_base=knowledge_base,
                file_id=file_id,
                data_id=f"d{index_chunk.data_chunk_index:06d}",
                index_id=f"d{index_chunk.data_chunk_index:06d}-{index_chunk.index_in_data:03d}",
                text=index_chunk.text,
                embedding=vector,
            )
            for index_chunk, vector in zip(index_chunks, index_vectors, strict=True)
        ]
        images_indexed = 0
        if document.images:
            for i, image in enumerate(document.images):
                try:
                    path = self._image_store.path_for(image.storage_key)
                    image_bytes = path.read_bytes()
                except OSError:
                    continue
                mime = _guess_mime(image.storage_key)
                desc = self._vlm.describe_image(
                    image_bytes,
                    mime_type=mime,
                    hint="文档图片/流程图",
                )
                if desc is None:
                    continue
                data_id = f"img{(image.page or 0):04d}-{image.index_in_page:03d}"
                chunk_index = len(data_chunks) + i
                data_records.append(
                    DataRecord(
                        knowledge_base=knowledge_base,
                        file_id=file_id,
                        data_id=data_id,
                        text=desc,
                        page=image.page,
                        chunk_index=chunk_index,
                        citation={
                            "file_name": file_name,
                            "page": image.page,
                            "data_id": data_id,
                            "type": "image",
                            "storage_key": image.storage_key,
                        },
                    )
                )
                index_records.append(
                    IndexRecord(
                        knowledge_base=knowledge_base,
                        file_id=file_id,
                        data_id=data_id,
                        index_id=f"{data_id}-000",
                        text=desc,
                        embedding=embedder.embed(desc),
                    )
                )
                images_indexed += 1
        self._vector_store.upsert_data_index(
            knowledge_base,
            file_id,
            data_records=data_records,
            index_records=index_records,
        )
        return {
            "data": len(data_records),
            "index": len(index_records),
            "images": images_indexed,
        }

    def search_data(
        self,
        *,
        knowledge_base: str,
        query: str,
        top_k: int,
        embedding_config: KbEmbeddingConfig | None = None,
    ) -> list[SearchHit]:
        embedder = self._embedder_factory.get(
            embedding_config or self._default_embedding_config
        )
        query_embedding = embedder.embed(query)
        hits = self._vector_store.search_data(knowledge_base, query_embedding, top_k)
        return [
            SearchHit(
                document_id=record.file_id,
                file_name=str(record.citation.get("file_name", "unknown")),
                chunk_id=record.data_id,
                text=record.text,
                score=score,
                citation=record.citation,
            )
            for record, score in hits
        ]


class _LangChainPgVectorRagPipeline:
    """LangChain + OpenAIEmbeddings + PGVectorStore 的 RAG 实现。"""

    def __init__(self, settings: Settings) -> None:
        if not settings.DATABASE_URL:
            raise ValueError("启用 langchain_pgvector 需要配置 DATABASE_URL。")

        from langchain_openai import OpenAIEmbeddings
        from langchain_postgres import PGEngine
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        self._settings = settings
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.RAG_CHUNK_SIZE,
            chunk_overlap=settings.RAG_CHUNK_OVERLAP,
        )
        self._embedding = OpenAIEmbeddings(
            model=settings.OPENAI_EMBEDDING_MODEL,
            dimensions=settings.RAG_EMBEDDING_DIMENSIONS,
        )
        self._engine = PGEngine.from_connection_string(url=settings.DATABASE_URL)
        self._index_path = settings.vector_store_path_resolved / "langchain_chunk_ids.json"
        self._index_path.parent.mkdir(parents=True, exist_ok=True)

    @traceable(name="langchain_rag_index_document", run_type="chain")
    def index_document(
        self,
        *,
        knowledge_base: str,
        file_name: str,
        text: str,
        content_type: str | None = None,
    ) -> DocumentMetadata:
        from langchain_core.documents import Document

        document = DocumentMetadata(
            document_id=str(uuid4()),
            file_name=file_name,
            content_type=content_type,
        )
        chunks = [chunk for chunk in self._splitter.split_text(text.strip()) if chunk.strip()]
        ids = [f"{document.document_id}:{i:06d}" for i in range(len(chunks))]
        docs = [
            Document(
                page_content=chunk,
                metadata={
                    "knowledge_base": knowledge_base,
                    "document_id": document.document_id,
                    "file_name": file_name,
                    "chunk_id": f"chunk-{i:06d}",
                },
            )
            for i, chunk in enumerate(chunks)
        ]
        if docs:
            store = self._get_store(knowledge_base)
            store.add_documents(documents=docs, ids=ids)
            self._save_chunk_ids(knowledge_base=knowledge_base, document_id=document.document_id, chunk_ids=ids)
        return document

    def delete_document(self, knowledge_base: str, document_id: str) -> None:
        chunk_ids = self._load_chunk_ids(knowledge_base=knowledge_base, document_id=document_id)
        if chunk_ids:
            store = self._get_store(knowledge_base)
            store.delete(ids=chunk_ids)
        self._clear_chunk_ids(knowledge_base=knowledge_base, document_id=document_id)

    def delete_knowledge_base(self, knowledge_base: str) -> None:
        # pgvector 数据默认保留，便于离线审计与回溯；仅清理文档到 chunk 映射索引。
        index = self._read_index()
        if knowledge_base in index:
            del index[knowledge_base]
            self._write_index(index)

    def count_chunks(self, knowledge_base: str) -> int:
        index = self._read_index().get(knowledge_base, {})
        return sum(len(chunk_ids) for chunk_ids in index.values())

    def count_chunks_by_document(self, knowledge_base: str) -> dict[str, int]:
        index = self._read_index().get(knowledge_base, {})
        return {document_id: len(chunk_ids) for document_id, chunk_ids in index.items()}

    @traceable(name="langchain_rag_search", run_type="retriever")
    def search(self, *, knowledge_base: str, query: str, top_k: int) -> list[SearchHit]:
        store = self._get_store(knowledge_base)
        docs_and_scores = store.similarity_search_with_relevance_scores(query, k=top_k)
        return [
            SearchHit(
                document_id=str(doc.metadata.get("document_id", "")),
                file_name=str(doc.metadata.get("file_name", "unknown")),
                chunk_id=str(doc.metadata.get("chunk_id", "chunk-unknown")),
                text=doc.page_content,
                score=float(score),
                citation={
                    "file_name": str(doc.metadata.get("file_name", "unknown")),
                    "chunk_id": str(doc.metadata.get("chunk_id", "chunk-unknown")),
                },
            )
            for doc, score in docs_and_scores
        ]

    def _get_store(self, knowledge_base: str):
        from langchain_postgres import PGVectorStore

        table_name = self._table_name(knowledge_base)
        self._engine.init_vectorstore_table(
            table_name=table_name,
            vector_size=self._settings.RAG_EMBEDDING_DIMENSIONS,
        )
        return PGVectorStore.create_sync(
            engine=self._engine,
            table_name=table_name,
            embedding_service=self._embedding,
        )

    @staticmethod
    def _table_name(knowledge_base: str) -> str:
        raw = re.sub(r"[^a-zA-Z0-9_]+", "_", knowledge_base.strip().lower()).strip("_")
        safe = raw or "default"
        return f"kb_{safe[:48]}"

    def _read_index(self) -> dict[str, dict[str, list[str]]]:
        if not self._index_path.exists():
            return {}
        data = json.loads(self._index_path.read_text(encoding="utf-8"))
        return {str(kb): {str(doc): [str(cid) for cid in ids] for doc, ids in docs.items()} for kb, docs in data.items()}

    def _write_index(self, payload: dict[str, dict[str, list[str]]]) -> None:
        self._index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_chunk_ids(self, *, knowledge_base: str, document_id: str, chunk_ids: list[str]) -> None:
        payload = self._read_index()
        payload.setdefault(knowledge_base, {})[document_id] = chunk_ids
        self._write_index(payload)

    def _load_chunk_ids(self, *, knowledge_base: str, document_id: str) -> list[str]:
        return self._read_index().get(knowledge_base, {}).get(document_id, [])

    def _clear_chunk_ids(self, *, knowledge_base: str, document_id: str) -> None:
        payload = self._read_index()
        docs = payload.get(knowledge_base)
        if not docs:
            return
        docs.pop(document_id, None)
        if not docs:
            payload.pop(knowledge_base, None)
        self._write_index(payload)


class RagPipeline:
    """统一 RAG 管线门面，根据配置切换 local / postgres_pgvector / langchain_pgvector。"""

    def __init__(self, settings: Settings) -> None:
        if settings.RAG_BACKEND == "langchain_pgvector":
            self._impl = _LangChainPgVectorRagPipeline(settings=settings)
        elif settings.RAG_BACKEND == "postgres_pgvector":
            self._impl = _HashRagPipeline(
                settings=settings,
                vector_store=PgVectorStore(
                    database_url=settings.DATABASE_URL,
                    dimensions=settings.RAG_EMBEDDING_DIMENSIONS,
                ),
                vlm_service=VlmService(settings=settings),
            )
        else:
            self._impl = _HashRagPipeline(
                settings=settings,
                vector_store=JsonVectorStore(settings.vector_store_path_resolved),
                vlm_service=VlmService(settings=settings),
            )

    def index_document(
        self,
        *,
        knowledge_base: str,
        file_name: str,
        text: str,
        content_type: str | None = None,
    ) -> DocumentMetadata:
        return self._impl.index_document(
            knowledge_base=knowledge_base,
            file_name=file_name,
            text=text,
            content_type=content_type,
        )

    def delete_document(self, knowledge_base: str, document_id: str) -> None:
        self._impl.delete_document(knowledge_base, document_id)

    def delete_knowledge_base(self, knowledge_base: str) -> None:
        self._impl.delete_knowledge_base(knowledge_base)

    def search(self, *, knowledge_base: str, query: str, top_k: int) -> list[SearchHit]:
        return self._impl.search(knowledge_base=knowledge_base, query=query, top_k=top_k)

    def count_chunks(self, knowledge_base: str) -> int:
        return self._impl.count_chunks(knowledge_base)

    def count_chunks_by_document(self, knowledge_base: str) -> dict[str, int]:
        return self._impl.count_chunks_by_document(knowledge_base)

    def index_data(
        self,
        *,
        knowledge_base: str,
        file_id: str,
        document: ParsedDocument,
        config: ChunkingConfig,
        file_name: str,
        embedding_config: KbEmbeddingConfig | None = None,
    ) -> dict[str, int]:
        if not hasattr(self._impl, "index_data"):
            raise NotImplementedError("当前 RAG_BACKEND 不支持 index_data")
        return self._impl.index_data(
            knowledge_base=knowledge_base,
            file_id=file_id,
            document=document,
            config=config,
            file_name=file_name,
            embedding_config=embedding_config,
        )

    def search_data(
        self,
        *,
        knowledge_base: str,
        query: str,
        top_k: int,
        embedding_config: KbEmbeddingConfig | None = None,
    ) -> list[SearchHit]:
        if not hasattr(self._impl, "search_data"):
            raise NotImplementedError("当前 RAG_BACKEND 不支持 search_data")
        return self._impl.search_data(
            knowledge_base=knowledge_base,
            query=query,
            top_k=top_k,
            embedding_config=embedding_config,
        )
