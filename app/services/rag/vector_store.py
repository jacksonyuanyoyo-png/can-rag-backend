from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class VectorRecord:
    knowledge_base: str
    document_id: str
    file_name: str
    chunk_id: str
    text: str
    embedding: list[float]
    citation: dict[str, Any]


class JsonVectorStore:
    """本地 JSON 向量索引，用于开发期验证 RAG 流程。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def upsert_document(self, knowledge_base: str, records: list[VectorRecord]) -> None:
        current = self._load(knowledge_base)
        document_ids = {record.document_id for record in records}
        current = [record for record in current if record.document_id not in document_ids]
        current.extend(records)
        self._save(knowledge_base, current)

    def delete_document(self, knowledge_base: str, document_id: str) -> None:
        current = [r for r in self._load(knowledge_base) if r.document_id != document_id]
        self._save(knowledge_base, current)

    def delete_knowledge_base(self, knowledge_base: str) -> None:
        path = self._path(knowledge_base)
        if path.exists():
            path.unlink()

    def search(self, knowledge_base: str, embedding: list[float], top_k: int) -> list[tuple[VectorRecord, float]]:
        scored = [
            (record, self._cosine(embedding, record.embedding))
            for record in self._load(knowledge_base)
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def _load(self, knowledge_base: str) -> list[VectorRecord]:
        path = self._path(knowledge_base)
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [
            VectorRecord(
                knowledge_base=str(item["knowledge_base"]),
                document_id=str(item["document_id"]),
                file_name=str(item["file_name"]),
                chunk_id=str(item["chunk_id"]),
                text=str(item["text"]),
                embedding=[float(v) for v in item["embedding"]],
                citation=dict(item.get("citation") or {}),
            )
            for item in raw.get("records", [])
        ]

    def _save(self, knowledge_base: str, records: list[VectorRecord]) -> None:
        payload = {
            "records": [
                {
                    "knowledge_base": record.knowledge_base,
                    "document_id": record.document_id,
                    "file_name": record.file_name,
                    "chunk_id": record.chunk_id,
                    "text": record.text,
                    "embedding": record.embedding,
                    "citation": record.citation,
                }
                for record in records
            ]
        }
        path = self._path(knowledge_base)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _path(self, knowledge_base: str) -> Path:
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in knowledge_base)
        return self._root / f"{safe_name}.json"

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        dot = sum(a * b for a, b in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)
