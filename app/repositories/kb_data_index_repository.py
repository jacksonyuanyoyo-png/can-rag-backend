from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.core.config import get_settings
from app.core.database import normalize_psycopg_url
from app.repositories.knowledge_base_stub import insert_knowledge_base_stub
from app.utils.text_sanitize import sanitize_for_postgres_json, sanitize_pg_text

logger = logging.getLogger(__name__)

_VECTOR_DIM_RE = re.compile(r"vector\((\d+)\)", re.IGNORECASE)


class KbDataIndexRepository:
    def __init__(
        self,
        database_url: str,
        *,
        connection: psycopg.Connection | None = None,
        dimensions: int | None = None,
    ) -> None:
        if not database_url.strip():
            raise ValueError("KbDataIndexRepository 需要配置 DATABASE_URL。")
        self._dsn = normalize_psycopg_url(database_url)
        self._external_conn = connection
        self._dimensions = (
            dimensions
            if dimensions is not None
            else get_settings().RAG_EMBEDDING_DIMENSIONS
        )

    def ensure_schema(self) -> None:
        dim = int(self._dimensions)
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute("CREATE SCHEMA IF NOT EXISTS app")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_dim_knowledge_base (
                        id              text PRIMARY KEY,
                        name            text NOT NULL,
                        description     text,
                        scope           text NOT NULL DEFAULT 'personal',
                        visibility      text NOT NULL DEFAULT 'private',
                        status          text NOT NULL DEFAULT 'active',
                        file_count      integer NOT NULL DEFAULT 0,
                        chunk_count     integer NOT NULL DEFAULT 0,
                        total_bytes     bigint NOT NULL DEFAULT 0,
                        created_at      timestamptz NOT NULL DEFAULT now(),
                        updated_at      timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_dim_kb_file (
                        id              text PRIMARY KEY,
                        kb_id           text NOT NULL
                            REFERENCES app.t_dim_knowledge_base (id) ON DELETE CASCADE,
                        file_name       text NOT NULL,
                        mime_type       text NOT NULL,
                        size_bytes      bigint NOT NULL DEFAULT 0,
                        storage_key     text NOT NULL,
                        status          text NOT NULL DEFAULT 'uploaded',
                        chunk_strategy  text NOT NULL DEFAULT 'fixed_size',
                        error_message   text,
                        created_at      timestamptz NOT NULL DEFAULT now(),
                        updated_at      timestamptz NOT NULL DEFAULT now(),
                        CONSTRAINT t_dim_kb_file_size_bytes_chk
                            CHECK (size_bytes >= 0),
                        CONSTRAINT t_dim_kb_file_status_chk
                            CHECK (status IN (
                                'uploaded', 'parsing', 'chunking',
                                'indexing', 'ready', 'failed'
                            ))
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS t_dim_kb_file_storage_key_uq
                    ON app.t_dim_kb_file (storage_key)
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS t_dim_kb_file_kb_id_file_name_uq
                    ON app.t_dim_kb_file (kb_id, file_name)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_fact_kb_data (
                        id              bigserial PRIMARY KEY,
                        kb_id           text NOT NULL
                            REFERENCES app.t_dim_knowledge_base (id) ON DELETE CASCADE,
                        file_id         text NOT NULL
                            REFERENCES app.t_dim_kb_file (id) ON DELETE CASCADE,
                        data_id         text NOT NULL,
                        text            text NOT NULL,
                        page            integer,
                        chunk_index     integer NOT NULL DEFAULT 0,
                        citation        jsonb NOT NULL DEFAULT '{}'::jsonb,
                        created_at      timestamptz NOT NULL DEFAULT now(),
                        updated_at      timestamptz NOT NULL DEFAULT now(),
                        CONSTRAINT t_fact_kb_data_chunk_index_chk
                            CHECK (chunk_index >= 0),
                        CONSTRAINT t_fact_kb_data_page_chk
                            CHECK (page IS NULL OR page >= 0),
                        CONSTRAINT t_fact_kb_data_kb_file_data_uq
                            UNIQUE (kb_id, file_id, data_id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_kb_data_kb_id_idx
                    ON app.t_fact_kb_data (kb_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_kb_data_file_id_idx
                    ON app.t_fact_kb_data (file_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_kb_data_kb_id_file_id_idx
                    ON app.t_fact_kb_data (kb_id, file_id)
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS app.t_fact_kb_index (
                        id              bigserial PRIMARY KEY,
                        kb_id           text NOT NULL
                            REFERENCES app.t_dim_knowledge_base (id) ON DELETE CASCADE,
                        file_id         text NOT NULL
                            REFERENCES app.t_dim_kb_file (id) ON DELETE CASCADE,
                        data_id         text NOT NULL,
                        index_id        text NOT NULL,
                        text            text NOT NULL,
                        embedding       vector({dim}) NOT NULL,
                        created_at      timestamptz NOT NULL DEFAULT now(),
                        updated_at      timestamptz NOT NULL DEFAULT now(),
                        CONSTRAINT t_fact_kb_index_kb_file_index_uq
                            UNIQUE (kb_id, file_id, index_id),
                        CONSTRAINT t_fact_kb_index_data_fkey
                            FOREIGN KEY (kb_id, file_id, data_id)
                            REFERENCES app.t_fact_kb_data (kb_id, file_id, data_id)
                            ON DELETE CASCADE
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_kb_index_kb_id_idx
                    ON app.t_fact_kb_index (kb_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_kb_index_file_id_idx
                    ON app.t_fact_kb_index (file_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_kb_index_kb_id_file_id_idx
                    ON app.t_fact_kb_index (kb_id, file_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_kb_index_kb_file_data_idx
                    ON app.t_fact_kb_index (kb_id, file_id, data_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_kb_index_embedding_hnsw_idx
                    ON app.t_fact_kb_index
                    USING hnsw (embedding vector_cosine_ops)
                    """
                )
                self._sync_embedding_column_dimension(cur, dim)

    @staticmethod
    def _parse_vector_column_dimension(coltype: str | None) -> int | None:
        if not coltype:
            return None
        match = _VECTOR_DIM_RE.search(coltype)
        return int(match.group(1)) if match else None

    def _sync_embedding_column_dimension(self, cur: psycopg.Cursor, target_dim: int) -> None:
        cur.execute(
            """
            SELECT format_type(a.atttypid, a.atttypmod) AS coltype
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'app'
              AND c.relname = 't_fact_kb_index'
              AND a.attname = 'embedding'
              AND NOT a.attisdropped
            """
        )
        row = cur.fetchone()
        if row is None:
            return
        current_dim = self._parse_vector_column_dimension(str(row.get("coltype") or ""))
        if current_dim is None or current_dim == target_dim:
            return
        logger.warning(
            "t_fact_kb_index.embedding 维度 %s 与 RAG_EMBEDDING_DIMENSIONS=%s 不一致，"
            "将清空索引向量并调整列类型（需重新导入文件）",
            current_dim,
            target_dim,
        )
        cur.execute(
            "DROP INDEX IF EXISTS app.t_fact_kb_index_embedding_hnsw_idx"
        )
        cur.execute("TRUNCATE app.t_fact_kb_index")
        cur.execute(
            f"""
            ALTER TABLE app.t_fact_kb_index
            ALTER COLUMN embedding TYPE vector({int(target_dim)})
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS t_fact_kb_index_embedding_hnsw_idx
            ON app.t_fact_kb_index
            USING hnsw (embedding vector_cosine_ops)
            """
        )

    def ensure_knowledge_base_stub(
        self,
        kb_id: str,
        *,
        name: str | None = None,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                insert_knowledge_base_stub(cur, kb_id, name=name)
            self._commit(conn)

    def ensure_kb_file_stub(
        self,
        *,
        kb_id: str,
        file_id: str,
        file_name: str | None = None,
    ) -> None:
        self.ensure_knowledge_base_stub(kb_id)
        display_name = (file_name or file_id).strip() or file_id
        storage_key = f"stub/{kb_id}/{file_id}"
        now = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_dim_kb_file (
                        id, kb_id, file_name, mime_type, size_bytes,
                        storage_key, status, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        file_id,
                        kb_id,
                        display_name,
                        "application/octet-stream",
                        0,
                        storage_key,
                        "uploaded",
                        now,
                        now,
                    ),
                )
            self._commit(conn)

    def upsert_data(
        self,
        *,
        kb_id: str,
        file_id: str,
        data_id: str,
        text: str,
        page: int | None,
        chunk_index: int,
        citation: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_fact_kb_data (
                        kb_id, file_id, data_id, text, page,
                        chunk_index, citation, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (kb_id, file_id, data_id) DO UPDATE SET
                        text = EXCLUDED.text,
                        page = EXCLUDED.page,
                        chunk_index = EXCLUDED.chunk_index,
                        citation = EXCLUDED.citation,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        kb_id,
                        file_id,
                        data_id,
                        sanitize_pg_text(text),
                        page,
                        chunk_index,
                        Json(sanitize_for_postgres_json(citation)),
                        now,
                    ),
                )
            self._commit(conn)

    def upsert_index(
        self,
        *,
        kb_id: str,
        file_id: str,
        data_id: str,
        index_id: str,
        text: str,
        embedding: list[float],
    ) -> None:
        vector = self._vector_literal(embedding)
        now = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_fact_kb_index (
                        kb_id, file_id, data_id, index_id, text,
                        embedding, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
                    ON CONFLICT (kb_id, file_id, index_id) DO UPDATE SET
                        data_id = EXCLUDED.data_id,
                        text = EXCLUDED.text,
                        embedding = EXCLUDED.embedding,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        kb_id,
                        file_id,
                        data_id,
                        index_id,
                        sanitize_pg_text(text),
                        vector,
                        now,
                    ),
                )
            self._commit(conn)

    def bulk_upsert_data(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.upsert_data(
                kb_id=str(row["kb_id"]),
                file_id=str(row["file_id"]),
                data_id=str(row["data_id"]),
                text=str(row["text"]),
                page=row.get("page"),
                chunk_index=int(row["chunk_index"]),
                citation=dict(row.get("citation") or {}),
            )

    def bulk_upsert_index(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.upsert_index(
                kb_id=str(row["kb_id"]),
                file_id=str(row["file_id"]),
                data_id=str(row["data_id"]),
                index_id=str(row["index_id"]),
                text=str(row["text"]),
                embedding=[float(v) for v in row["embedding"]],
            )

    def count_data_chunks_by_file(self, kb_id: str) -> dict[str, int]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT file_id, COUNT(*) AS count
                    FROM app.t_fact_kb_data
                    WHERE kb_id = %s
                    GROUP BY file_id
                    """,
                    (kb_id,),
                )
                rows = cur.fetchall() or []
        return {str(row["file_id"]): int(row["count"]) for row in rows}

    def list_data_by_file(self, kb_id: str, file_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_fact_kb_data
                    WHERE kb_id = %s AND file_id = %s
                    ORDER BY chunk_index ASC, data_id ASC
                    """,
                    (kb_id, file_id),
                )
                rows = cur.fetchall() or []
        return [self._normalize_data_row(dict(row)) for row in rows]

    def get_data(
        self,
        kb_id: str,
        file_id: str,
        data_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_fact_kb_data
                    WHERE kb_id = %s AND file_id = %s AND data_id = %s
                    """,
                    (kb_id, file_id, data_id),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return self._normalize_data_row(dict(row))

    def list_index_by_data(
        self,
        kb_id: str,
        file_id: str,
        data_id: str,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id, kb_id, file_id, data_id, index_id, text,
                        embedding::text AS embedding,
                        created_at, updated_at
                    FROM app.t_fact_kb_index
                    WHERE kb_id = %s AND file_id = %s AND data_id = %s
                    ORDER BY index_id ASC
                    """,
                    (kb_id, file_id, data_id),
                )
                rows = cur.fetchall() or []
        return [self._normalize_index_row(dict(row)) for row in rows]

    def delete_by_file(self, kb_id: str, file_id: str) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM app.t_fact_kb_data
                    WHERE kb_id = %s AND file_id = %s
                    """,
                    (kb_id, file_id),
                )
                deleted = cur.rowcount
            self._commit(conn)
        return int(deleted)

    def delete_by_data(self, kb_id: str, file_id: str, data_id: str) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM app.t_fact_kb_data
                    WHERE kb_id = %s AND file_id = %s AND data_id = %s
                    """,
                    (kb_id, file_id, data_id),
                )
                deleted = cur.rowcount
            self._commit(conn)
        return int(deleted)

    def _vector_literal(self, embedding: list[float]) -> str:
        if len(embedding) != self._dimensions:
            raise ValueError(
                f"向量维度不匹配: expected={self._dimensions}, actual={len(embedding)}"
            )
        return "[" + ",".join(str(float(value)) for value in embedding) + "]"

    @staticmethod
    def _normalize_data_row(row: dict[str, Any]) -> dict[str, Any]:
        citation = row.get("citation")
        if citation is not None and not isinstance(citation, dict):
            citation = dict(citation)
        row["citation"] = citation or {}
        return row

    def _normalize_index_row(self, row: dict[str, Any]) -> dict[str, Any]:
        embedding_raw = row.get("embedding")
        if isinstance(embedding_raw, str):
            row["embedding"] = self._parse_vector(embedding_raw)
        elif embedding_raw is not None:
            row["embedding"] = [float(v) for v in embedding_raw]
        return row

    @staticmethod
    def _parse_vector(value: str) -> list[float]:
        text = value.strip()
        if not text:
            return []
        return [float(item) for item in text.strip("[]").split(",") if item]

    def _commit(self, conn: psycopg.Connection) -> None:
        if self._external_conn is None:
            conn.commit()

    @contextmanager
    def _connect(self, *, autocommit: bool = False) -> Iterator[psycopg.Connection]:
        if self._external_conn is not None:
            yield self._external_conn
            return
        with psycopg.connect(
            self._dsn,
            row_factory=dict_row,
            autocommit=autocommit,
        ) as conn:
            yield conn
