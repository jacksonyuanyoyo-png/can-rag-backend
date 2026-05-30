from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class BackendType(StrEnum):
    LOCAL = "local"
    FASTGPT = "fastgpt"
    OPENAI = "openai"
    HYBRID = "hybrid"


EMBEDDING_MODEL_ID_KEY = "embedding_model_id"
RESOURCE_TYPE_KEY = "resource_type"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class DocumentMetadata:
    document_id: str
    file_name: str
    content_type: str | None = None
    backend_refs: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "file_name": self.file_name,
            "content_type": self.content_type,
            "backend_refs": self.backend_refs,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentMetadata":
        return cls(
            document_id=str(data["document_id"]),
            file_name=str(data["file_name"]),
            content_type=data.get("content_type"),
            backend_refs=dict(data.get("backend_refs") or {}),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
        )


@dataclass(slots=True)
class KnowledgeBaseMetadata:
    name: str
    id: str = field(default_factory=lambda: str(uuid4()))
    backend: BackendType = BackendType.LOCAL
    description: str = ""
    backend_refs: dict[str, Any] = field(default_factory=dict)
    documents: dict[str, DocumentMetadata] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "backend": self.backend.value,
            "description": self.description,
            "backend_refs": self.backend_refs,
            "documents": {
                document_id: document.to_dict()
                for document_id, document in self.documents.items()
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeBaseMetadata":
        documents = {
            document_id: DocumentMetadata.from_dict(document)
            for document_id, document in dict(data.get("documents") or {}).items()
        }
        name = str(data["name"])
        return cls(
            name=name,
            id=str(data.get("id") or name),
            backend=BackendType(str(data.get("backend") or BackendType.LOCAL)),
            description=str(data.get("description") or ""),
            backend_refs=dict(data.get("backend_refs") or {}),
            documents=documents,
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
        )


@dataclass(slots=True)
class SearchHit:
    document_id: str
    file_name: str
    chunk_id: str
    text: str
    score: float
    citation: dict[str, Any]
