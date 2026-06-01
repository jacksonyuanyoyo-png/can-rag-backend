from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.core.database import normalize_psycopg_url
from app.domain.upload import KnowledgeBaseFileRecord, UploadObject, UploadObjectStatus


class UploadRepository:
    """上传对象 PostgreSQL 仓储，对齐 t_fact_upload_object / t_dim_kb_file。"""

    def __init__(
        self,
        database_url: str,
        *,
        connection: psycopg.Connection | None = None,
    ) -> None:
        if not database_url.strip():
            raise ValueError("UploadRepository 需要配置 DATABASE_URL。")
        self._dsn = normalize_psycopg_url(database_url)
        self._external_conn = connection

    def ensure_schema(self) -> None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS app")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_fact_upload_object (
                        id              text PRIMARY KEY,
                        kb_id           text NOT NULL,
                        user_id         text NOT NULL,
                        file_id         text NOT NULL,
                        file_name       text NOT NULL,
                        mime_type       text NOT NULL,
                        size_bytes      bigint NOT NULL DEFAULT 0,
                        storage_key     text NOT NULL,
                        upload_url      text,
                        etag            text,
                        status          text NOT NULL DEFAULT 'pending',
                        expires_at      timestamptz,
                        completed_at    timestamptz,
                        created_at      timestamptz NOT NULL DEFAULT now(),
                        updated_at      timestamptz NOT NULL DEFAULT now(),
                        CONSTRAINT t_fact_upload_object_size_bytes_chk
                            CHECK (size_bytes >= 0),
                        CONSTRAINT t_fact_upload_object_status_chk
                            CHECK (status IN (
                                'pending', 'uploading', 'uploaded',
                                'completed', 'failed', 'expired'
                            ))
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS t_fact_upload_object_storage_key_uq
                    ON app.t_fact_upload_object (storage_key)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_upload_object_kb_id_idx
                    ON app.t_fact_upload_object (kb_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_upload_object_user_id_idx
                    ON app.t_fact_upload_object (user_id)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_dim_kb_file (
                        id              text PRIMARY KEY,
                        kb_id           text NOT NULL,
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

    def ensure_knowledge_base_stub(
        self,
        kb_id: str,
        *,
        name: str | None = None,
    ) -> None:
        """确保 PG 中存在知识库主数据，满足 t_dim_kb_file 外键约束。"""
        display_name = (name or kb_id).strip() or kb_id
        now = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_dim_knowledge_base (
                        id, name, scope, visibility, status,
                        created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        kb_id,
                        display_name,
                        "personal",
                        "private",
                        "active",
                        now,
                        now,
                    ),
                )
            self._commit(conn)

    def delete_upload_sessions_for_storage_key(self, storage_key: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM app.t_fact_upload_object
                    WHERE storage_key = %s
                    """,
                    (storage_key,),
                )
            self._commit(conn)

    def create_upload_object(
        self,
        *,
        kb_id: str,
        user_id: str,
        file_id: str,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        storage_key: str,
        upload_url: str,
        expires_at: datetime,
        upload_id: str | None = None,
    ) -> UploadObject:
        upload_id = upload_id or f"upl_{uuid4().hex}"
        now = datetime.now(UTC)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_fact_upload_object (
                        id, kb_id, user_id, file_id, file_name, mime_type,
                        size_bytes, storage_key, upload_url, status,
                        expires_at, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        upload_id,
                        kb_id,
                        user_id,
                        file_id,
                        file_name,
                        mime_type,
                        size_bytes,
                        storage_key,
                        upload_url,
                        UploadObjectStatus.PENDING.value,
                        expires_at,
                        now,
                        now,
                    ),
                )
                row = cur.fetchone()
            self._commit(conn)

        if row is None:
            raise RuntimeError("创建上传对象失败。")
        return UploadObject.from_row(dict(row))

    def get_upload_object(self, upload_id: str) -> UploadObject | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_fact_upload_object
                    WHERE id = %s
                    """,
                    (upload_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return UploadObject.from_row(dict(row))

    def require_upload_object(self, upload_id: str) -> UploadObject:
        upload = self.get_upload_object(upload_id)
        if upload is None:
            raise ValueError(f"上传对象不存在: {upload_id}")
        return upload

    def mark_upload_object_uploaded(
        self,
        upload_id: str,
        *,
        etag: str | None = None,
    ) -> UploadObject | None:
        now = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.t_fact_upload_object
                    SET status = %s,
                        etag = %s,
                        completed_at = %s,
                        updated_at = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        UploadObjectStatus.UPLOADED.value,
                        etag,
                        now,
                        now,
                        upload_id,
                    ),
                )
                row = cur.fetchone()
            self._commit(conn)

        if row is None:
            return None
        return UploadObject.from_row(dict(row))

    def get_kb_file_by_name(self, kb_id: str, file_name: str) -> KnowledgeBaseFileRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_kb_file
                    WHERE kb_id = %s AND file_name = %s
                    """,
                    (kb_id, file_name),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return KnowledgeBaseFileRecord.from_row(dict(row))

    def get_kb_file(self, file_id: str) -> KnowledgeBaseFileRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_kb_file
                    WHERE id = %s
                    """,
                    (file_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return KnowledgeBaseFileRecord.from_row(dict(row))

    def count_kb_files(self, kb_id: str) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM app.t_dim_kb_file
                    WHERE kb_id = %s
                    """,
                    (kb_id,),
                )
                row = cur.fetchone()
        if row is None:
            return 0
        return int(row["count"])

    def list_kb_files(self, kb_id: str) -> list[KnowledgeBaseFileRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_kb_file
                    WHERE kb_id = %s
                    ORDER BY created_at DESC, id DESC
                    """,
                    (kb_id,),
                )
                rows = cur.fetchall() or []
        return [KnowledgeBaseFileRecord.from_row(dict(row)) for row in rows]

    def get_kb_aggregate_stats(self, kb_id: str) -> dict[str, int | None] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT file_count, chunk_count
                    FROM app.t_dim_knowledge_base
                    WHERE id = %s
                    """,
                    (kb_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return {
            "file_count": int(row["file_count"]),
            "chunk_count": int(row["chunk_count"]),
        }

    def get_kb_file_counts(self, kb_ids: list[str]) -> dict[str, int]:
        if not kb_ids:
            return {}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT kb_id, COUNT(*) AS count
                    FROM app.t_dim_kb_file
                    WHERE kb_id = ANY(%s)
                    GROUP BY kb_id
                    """,
                    (kb_ids,),
                )
                rows = cur.fetchall() or []
        return {str(row["kb_id"]): int(row["count"]) for row in rows}

    def create_kb_file(
        self,
        *,
        kb_id: str,
        file_id: str,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        storage_key: str,
        status: str = "uploaded",
    ) -> KnowledgeBaseFileRecord:
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
                    RETURNING *
                    """,
                    (
                        file_id,
                        kb_id,
                        file_name,
                        mime_type,
                        size_bytes,
                        storage_key,
                        status,
                        now,
                        now,
                    ),
                )
                row = cur.fetchone()
            self._commit(conn)

        if row is None:
            raise RuntimeError("创建知识库文件记录失败。")
        return KnowledgeBaseFileRecord.from_row(dict(row))

    def delete_kb_file(self, *, kb_id: str, file_id: str) -> bool:
        """删除知识库文件元数据；关联切片/向量由 FK ON DELETE CASCADE 清理。"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM app.t_fact_upload_object
                    WHERE kb_id = %s AND file_id = %s
                    """,
                    (kb_id, file_id),
                )
                cur.execute(
                    """
                    DELETE FROM app.t_dim_kb_file
                    WHERE kb_id = %s AND id = %s
                    """,
                    (kb_id, file_id),
                )
                deleted = cur.rowcount > 0
            self._commit(conn)
        return deleted

    def update_kb_file(
        self,
        *,
        file_id: str,
        mime_type: str,
        size_bytes: int,
        status: str,
    ) -> KnowledgeBaseFileRecord | None:
        now = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.t_dim_kb_file
                    SET mime_type = %s,
                        size_bytes = %s,
                        status = %s,
                        updated_at = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (mime_type, size_bytes, status, now, file_id),
                )
                row = cur.fetchone()
            self._commit(conn)

        if row is None:
            return None
        return KnowledgeBaseFileRecord.from_row(dict(row))

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
