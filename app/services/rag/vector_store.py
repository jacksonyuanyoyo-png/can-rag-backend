from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.database import normalize_psycopg_url
from app.repositories.kb_data_index_repository import KbDataIndexRepository
from app.utils.text_sanitize import sanitize_for_postgres_json, sanitize_pg_text


@dataclass(slots=True)
class DataRecord:
    knowledge_base: str
    file_id: str
    data_id: str
    text: str
    page: int | None
    chunk_index: int
    citation: dict[str, Any]


@dataclass(slots=True)
class IndexRecord:
    knowledge_base: str
    file_id: str
    data_id: str
    index_id: str
    text: str
    embedding: list[float]


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

    def count_chunks(self, knowledge_base: str) -> int:
        return len(self._load(knowledge_base))

    def count_chunks_by_document(self, knowledge_base: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self._load(knowledge_base):
            counts[record.document_id] = counts.get(record.document_id, 0) + 1
        return counts

    def upsert_data_index(
        self,
        knowledge_base: str,
        file_id: str,
        *,
        data_records: list[DataRecord],
        index_records: list[IndexRecord],
    ) -> None:
        data_rows, index_rows = self._load_dataindex(knowledge_base)
        data_rows = [row for row in data_rows if row.get("file_id") != file_id]
        index_rows = [row for row in index_rows if row.get("file_id") != file_id]
        data_rows.extend(self._serialize_data_record(record) for record in data_records)
        index_rows.extend(self._serialize_index_record(record) for record in index_records)
        self._save_dataindex(knowledge_base, data_rows, index_rows)

    def search_data(
        self,
        knowledge_base: str,
        embedding: list[float],
        top_k: int,
    ) -> list[tuple[DataRecord, float]]:
        data_rows, index_rows = self._load_dataindex(knowledge_base)
        best_scores: dict[tuple[str, str], float] = {}
        for row in index_rows:
            score = self._cosine(embedding, [float(v) for v in row["embedding"]])
            key = (str(row["file_id"]), str(row["data_id"]))
            prev = best_scores.get(key)
            if prev is None or score > prev:
                best_scores[key] = score
        ranked = sorted(best_scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
        data_lookup = {
            (str(row["file_id"]), str(row["data_id"])): row for row in data_rows
        }
        results: list[tuple[DataRecord, float]] = []
        for (file_id, data_id), score in ranked:
            row = data_lookup.get((file_id, data_id))
            if row is None:
                continue
            results.append((self._data_record_from_row(knowledge_base, row), score))
        return results

    def delete_file(self, knowledge_base: str, file_id: str) -> None:
        data_rows, index_rows = self._load_dataindex(knowledge_base)
        data_rows = [row for row in data_rows if row.get("file_id") != file_id]
        index_rows = [row for row in index_rows if row.get("file_id") != file_id]
        self._save_dataindex(knowledge_base, data_rows, index_rows)

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

    def _dataindex_path(self, knowledge_base: str) -> Path:
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in knowledge_base)
        return self._root / f"{safe_name}.dataindex.json"

    def _load_dataindex(self, knowledge_base: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        path = self._dataindex_path(knowledge_base)
        if not path.exists():
            return [], []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return list(raw.get("data") or []), list(raw.get("index") or [])

    def _save_dataindex(
        self,
        knowledge_base: str,
        data_rows: list[dict[str, Any]],
        index_rows: list[dict[str, Any]],
    ) -> None:
        path = self._dataindex_path(knowledge_base)
        payload = {"data": data_rows, "index": index_rows}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _serialize_data_record(record: DataRecord) -> dict[str, Any]:
        return {
            "knowledge_base": record.knowledge_base,
            "file_id": record.file_id,
            "data_id": record.data_id,
            "text": record.text,
            "page": record.page,
            "chunk_index": record.chunk_index,
            "citation": record.citation,
        }

    @staticmethod
    def _serialize_index_record(record: IndexRecord) -> dict[str, Any]:
        return {
            "knowledge_base": record.knowledge_base,
            "file_id": record.file_id,
            "data_id": record.data_id,
            "index_id": record.index_id,
            "text": record.text,
            "embedding": record.embedding,
        }

    @staticmethod
    def _data_record_from_row(knowledge_base: str, row: dict[str, Any]) -> DataRecord:
        return DataRecord(
            knowledge_base=knowledge_base,
            file_id=str(row["file_id"]),
            data_id=str(row["data_id"]),
            text=str(row["text"]),
            page=row.get("page"),
            chunk_index=int(row["chunk_index"]),
            citation=dict(row.get("citation") or {}),
        )

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


class PgVectorStore:
    """PostgreSQL + pgvector 向量索引，用于本地和生产前集成验证。"""

    def __init__(self, database_url: str, dimensions: int) -> None:
        if not database_url:
            raise ValueError("启用 postgres_pgvector 需要配置 DATABASE_URL。")
        self._database_url = database_url
        self._dsn = normalize_psycopg_url(database_url)
        self._dimensions = dimensions
        self._ensure_schema()

    def upsert_document(self, knowledge_base: str, records: list[VectorRecord]) -> None:
        if not records:
            return
        document_ids = {record.document_id for record in records}
        with psycopg.connect(self._dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM app.rag_chunks
                    WHERE knowledge_base = %s
                      AND document_id = ANY(%s)
                    """,
                    (knowledge_base, list(document_ids)),
                )
                cur.executemany(
                    """
                    INSERT INTO app.rag_chunks (
                        knowledge_base,
                        document_id,
                        file_name,
                        chunk_id,
                        text,
                        embedding,
                        citation
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::vector, %s::jsonb)
                    ON CONFLICT (knowledge_base, document_id, chunk_id)
                    DO UPDATE SET
                        file_name = EXCLUDED.file_name,
                        text = EXCLUDED.text,
                        embedding = EXCLUDED.embedding,
                        citation = EXCLUDED.citation,
                        updated_at = now()
                    """,
                    [
                        (
                            record.knowledge_base,
                            record.document_id,
                            record.file_name,
                            record.chunk_id,
                            sanitize_pg_text(record.text),
                            self._vector_literal(record.embedding),
                            json.dumps(
                                sanitize_for_postgres_json(record.citation),
                                ensure_ascii=False,
                            ),
                        )
                        for record in records
                    ],
                )

    def delete_document(self, knowledge_base: str, document_id: str) -> None:
        with psycopg.connect(self._dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM app.rag_chunks
                    WHERE knowledge_base = %s
                      AND document_id = %s
                    """,
                    (knowledge_base, document_id),
                )

    def delete_knowledge_base(self, knowledge_base: str) -> None:
        with psycopg.connect(self._dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM app.rag_chunks WHERE knowledge_base = %s",
                    (knowledge_base,),
                )

    def count_chunks(self, knowledge_base: str) -> int:
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM app.rag_chunks WHERE knowledge_base = %s",
                    (knowledge_base,),
                )
                row = cur.fetchone()
        return int(row[0]) if row else 0

    def count_chunks_by_document(self, knowledge_base: str) -> dict[str, int]:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT document_id, COUNT(*) AS chunk_count
                    FROM app.rag_chunks
                    WHERE knowledge_base = %s
                    GROUP BY document_id
                    """,
                    (knowledge_base,),
                )
                rows = cur.fetchall()
        return {str(row["document_id"]): int(row["chunk_count"]) for row in rows}

    def search(self, knowledge_base: str, embedding: list[float], top_k: int) -> list[tuple[VectorRecord, float]]:
        vector = self._vector_literal(embedding)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        knowledge_base,
                        document_id,
                        file_name,
                        chunk_id,
                        text,
                        embedding::text AS embedding,
                        citation,
                        1 - (embedding <=> %s::vector) AS score
                    FROM app.rag_chunks
                    WHERE knowledge_base = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vector, knowledge_base, vector, top_k),
                )
                rows = cur.fetchall()

        return [
            (
                VectorRecord(
                    knowledge_base=str(row["knowledge_base"]),
                    document_id=str(row["document_id"]),
                    file_name=str(row["file_name"]),
                    chunk_id=str(row["chunk_id"]),
                    text=str(row["text"]),
                    embedding=self._parse_vector(str(row["embedding"])),
                    citation=dict(row.get("citation") or {}),
                ),
                float(row["score"]),
            )
            for row in rows
        ]

    def upsert_data_index(
        self,
        knowledge_base: str,
        file_id: str,
        *,
        data_records: list[DataRecord],
        index_records: list[IndexRecord],
    ) -> None:
        repo = KbDataIndexRepository(self._database_url, dimensions=self._dimensions)
        repo.ensure_schema()
        repo.ensure_kb_file_stub(kb_id=knowledge_base, file_id=file_id)
        repo.delete_by_file(knowledge_base, file_id)
        if data_records:
            repo.bulk_upsert_data(
                [
                    {
                        "kb_id": knowledge_base,
                        "file_id": file_id,
                        "data_id": record.data_id,
                        "text": record.text,
                        "page": record.page,
                        "chunk_index": record.chunk_index,
                        "citation": record.citation,
                    }
                    for record in data_records
                ]
            )
        if index_records:
            repo.bulk_upsert_index(
                [
                    {
                        "kb_id": knowledge_base,
                        "file_id": file_id,
                        "data_id": record.data_id,
                        "index_id": record.index_id,
                        "text": record.text,
                        "embedding": record.embedding,
                    }
                    for record in index_records
                ]
            )

    def search_data(
        self,
        knowledge_base: str,
        embedding: list[float],
        top_k: int,
    ) -> list[tuple[DataRecord, float]]:
        vector = self._vector_literal(embedding)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT d.kb_id, d.file_id, d.data_id, d.text, d.page, d.chunk_index, d.citation,
                           MAX(1 - (i.embedding <=> %s::vector)) AS score
                    FROM app.t_fact_kb_index i
                    JOIN app.t_fact_kb_data d
                      ON d.kb_id=i.kb_id AND d.file_id=i.file_id AND d.data_id=i.data_id
                    WHERE d.kb_id = %s
                    GROUP BY d.kb_id, d.file_id, d.data_id, d.text, d.page, d.chunk_index, d.citation
                    ORDER BY score DESC
                    LIMIT %s
                    """,
                    (vector, knowledge_base, top_k),
                )
                rows = cur.fetchall()
        return [
            (
                DataRecord(
                    knowledge_base=str(row["kb_id"]),
                    file_id=str(row["file_id"]),
                    data_id=str(row["data_id"]),
                    text=str(row["text"]),
                    page=row.get("page"),
                    chunk_index=int(row["chunk_index"]),
                    citation=dict(row.get("citation") or {}),
                ),
                float(row["score"]),
            )
            for row in rows
        ]

    def delete_file(self, knowledge_base: str, file_id: str) -> None:
        repo = KbDataIndexRepository(self._database_url, dimensions=self._dimensions)
        repo.delete_by_file(knowledge_base, file_id)

    def _ensure_schema(self) -> None:
        with psycopg.connect(self._dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute("CREATE SCHEMA IF NOT EXISTS app")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS app.rag_chunks (
                        id bigserial PRIMARY KEY,
                        knowledge_base text NOT NULL,
                        document_id text NOT NULL,
                        file_name text NOT NULL,
                        chunk_id text NOT NULL,
                        text text NOT NULL,
                        embedding vector({int(self._dimensions)}) NOT NULL,
                        citation jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        UNIQUE (knowledge_base, document_id, chunk_id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS rag_chunks_knowledge_base_idx
                    ON app.rag_chunks (knowledge_base)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS rag_chunks_embedding_hnsw_idx
                    ON app.rag_chunks
                    USING hnsw (embedding vector_cosine_ops)
                    """
                )

    def _vector_literal(self, embedding: list[float]) -> str:
        if len(embedding) != self._dimensions:
            raise ValueError(f"向量维度不匹配: expected={self._dimensions}, actual={len(embedding)}")
        return "[" + ",".join(str(float(value)) for value in embedding) + "]"

    @staticmethod
    def _parse_vector(value: str) -> list[float]:
        text = value.strip()
        if not text:
            return []
        return [float(item) for item in text.strip("[]").split(",") if item]
