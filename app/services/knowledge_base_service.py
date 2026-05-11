from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.domain.knowledge_base import BackendType, KnowledgeBaseMetadata, SearchHit
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.services.rag.pipeline import RagPipeline


class KnowledgeBaseService:
    """知识库用例服务。

    该层不直接暴露 HTTP 接口，负责协调元数据仓储、文档存储与 RAG 管线。
    """

    def __init__(
        self,
        *,
        settings: Settings,
        repository: KnowledgeBaseRepository,
        rag_pipeline: RagPipeline,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._rag_pipeline = rag_pipeline

    def list_kbs(self) -> list[KnowledgeBaseMetadata]:
        return self._repository.list()

    def create_kb(
        self,
        *,
        name: str,
        backend: BackendType = BackendType.LOCAL,
        description: str = "",
    ) -> KnowledgeBaseMetadata:
        if self._repository.get(name) is not None:
            raise ValueError(f"知识库已存在: {name}")
        metadata = KnowledgeBaseMetadata(name=name, backend=backend, description=description)
        self._kb_dir(name).mkdir(parents=True, exist_ok=True)
        return self._repository.save(metadata)

    def delete_kb(self, name: str) -> None:
        self._repository.require(name)
        root = self._kb_dir(name)
        if root.exists():
            for child in root.iterdir():
                if child.is_file():
                    child.unlink()
            try:
                root.rmdir()
            except OSError:
                pass
        self._rag_pipeline.delete_knowledge_base(name)
        self._repository.delete(name)

    def update_description(self, *, name: str, description: str) -> KnowledgeBaseMetadata:
        metadata = self._repository.require(name)
        metadata.description = description
        return self._repository.save(metadata)

    def index_document(
        self,
        *,
        knowledge_base: str,
        file_name: str,
        content: bytes,
        content_type: str | None = None,
    ) -> KnowledgeBaseMetadata:
        metadata = self._repository.require(knowledge_base)
        destination = self._kb_dir(knowledge_base) / file_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        text = self._read_text(destination)
        document = self._rag_pipeline.index_document(
            knowledge_base=knowledge_base,
            file_name=file_name,
            text=text,
            content_type=content_type,
        )
        metadata.documents[document.document_id] = document
        return self._repository.save(metadata)

    def delete_document(self, *, knowledge_base: str, document_id: str) -> KnowledgeBaseMetadata:
        metadata = self._repository.require(knowledge_base)
        document = metadata.documents.pop(document_id, None)
        if document is None:
            raise ValueError(f"文档不存在: {document_id}")
        path = self._kb_dir(knowledge_base) / document.file_name
        if path.exists():
            path.unlink()
        self._rag_pipeline.delete_document(knowledge_base, document_id)
        return self._repository.save(metadata)

    def search(self, *, knowledge_base: str, query: str, top_k: int = 5) -> list[SearchHit]:
        self._repository.require(knowledge_base)
        return self._rag_pipeline.search(knowledge_base=knowledge_base, query=query, top_k=top_k)

    def _kb_dir(self, kb_name: str) -> Path:
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in kb_name)
        return self._settings.upload_root_resolved / safe_name

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")
