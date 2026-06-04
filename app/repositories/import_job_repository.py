from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.core.database import normalize_psycopg_url
from app.repositories.knowledge_base_stub import insert_knowledge_base_stub
from app.utils.text_sanitize import sanitize_pg_text
from app.domain.import_job import (
    CANCELLABLE_STATUSES,
    ImportJob,
    ImportJobFileStatus,
    ImportJobOption,
    ImportJobStage,
    ImportJobStatus,
    ImportJobTransitionError,
    validate_stage_transition,
    validate_status_transition,
)


class ImportJobRepository:
    """导入任务 PostgreSQL 仓储，使用原生 SQL 与 DATABASE_URL 兼容。"""

    def __init__(
        self,
        database_url: str,
        *,
        connection: psycopg.Connection | None = None,
    ) -> None:
        if not database_url.strip():
            raise ValueError("ImportJobRepository 需要配置 DATABASE_URL。")
        self._dsn = normalize_psycopg_url(database_url)
        self._external_conn = connection

    def ensure_schema(self) -> None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS app")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_fact_import_job (
                        id text PRIMARY KEY,
                        kb_id text NOT NULL,
                        status text NOT NULL,
                        progress smallint NOT NULL DEFAULT 0
                            CHECK (progress >= 0 AND progress <= 100),
                        stage text NOT NULL,
                        error_code text,
                        error_message text,
                        retry_of text REFERENCES app.t_fact_import_job(id),
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE app.t_fact_import_job
                    ADD COLUMN IF NOT EXISTS retry_of text
                        REFERENCES app.t_fact_import_job(id)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_fact_import_job_file (
                        id text PRIMARY KEY,
                        import_job_id text NOT NULL
                            REFERENCES app.t_fact_import_job(id) ON DELETE CASCADE,
                        file_id text NOT NULL,
                        file_status text NOT NULL DEFAULT 'pending',
                        error_code text,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        UNIQUE (import_job_id, file_id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_fact_import_job_option (
                        id text PRIMARY KEY,
                        import_job_id text NOT NULL UNIQUE
                            REFERENCES app.t_fact_import_job(id) ON DELETE CASCADE,
                        chunk_strategy text NOT NULL,
                        meta_filename boolean NOT NULL DEFAULT true,
                        meta_headings boolean NOT NULL DEFAULT false,
                        created_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE app.t_fact_import_job_option
                    ADD COLUMN IF NOT EXISTS chunking_config jsonb
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_import_job_kb_id_idx
                    ON app.t_fact_import_job (kb_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_import_job_kb_status_idx
                    ON app.t_fact_import_job (kb_id, status)
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
        """确保 PG 中存在知识库主数据，满足 import job 外键约束。"""
        with self._connect() as conn:
            with conn.cursor() as cur:
                insert_knowledge_base_stub(cur, kb_id, name=name)
            self._commit(conn)

    def create(
        self,
        *,
        kb_id: str,
        file_ids: list[str],
        chunk_strategy: str,
        meta_filename: bool = True,
        meta_headings: bool = False,
        retry_of: str | None = None,
        job_id: str | None = None,
        chunking_config: dict | None = None,
    ) -> ImportJob:
        if not file_ids:
            raise ValueError("导入任务至少需要一个 file_id。")

        job_id = job_id or f"job_{uuid4().hex}"
        option_id = f"jobopt_{uuid4().hex}"
        now = datetime.now(UTC)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_fact_import_job (
                        id, kb_id, status, progress, stage, retry_of, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        job_id,
                        kb_id,
                        ImportJobStatus.QUEUED.value,
                        0,
                        ImportJobStage.UPLOAD.value,
                        retry_of,
                        now,
                        now,
                    ),
                )

                for file_id in file_ids:
                    cur.execute(
                        """
                        INSERT INTO app.t_fact_import_job_file (
                            id, import_job_id, file_id, file_status
                        )
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            f"jobfile_{uuid4().hex}",
                            job_id,
                            file_id,
                            ImportJobFileStatus.PENDING.value,
                        ),
                    )

                cur.execute(
                    """
                    INSERT INTO app.t_fact_import_job_option (
                        id, import_job_id, chunk_strategy, meta_filename, meta_headings,
                        chunking_config
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        option_id,
                        job_id,
                        chunk_strategy,
                        meta_filename,
                        meta_headings,
                        Json(chunking_config) if chunking_config is not None else None,
                    ),
                )
            self._commit(conn)

        return self.require(job_id)

    def get(self, job_id: str) -> ImportJob | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_fact_import_job
                    WHERE id = %s
                    """,
                    (job_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                file_ids = self._fetch_file_ids(cur, job_id)
                option = self._fetch_option(cur, job_id)
                return ImportJob.from_row(dict(row), file_ids=file_ids, option=option)

    def require(self, job_id: str) -> ImportJob:
        job = self.get(job_id)
        if job is None:
            raise ValueError(f"导入任务不存在: {job_id}")
        return job

    def get_chunking_config(self, import_job_id: str) -> dict | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT chunking_config
                    FROM app.t_fact_import_job_option
                    WHERE import_job_id = %s
                    """,
                    (import_job_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                value = row["chunking_config"]
                if value is None:
                    return None
                if isinstance(value, dict):
                    return value
                return dict(value)

    def update_progress(
        self,
        job_id: str,
        *,
        status: ImportJobStatus | None = None,
        progress: int | None = None,
        stage: ImportJobStage | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        clear_error: bool = False,
    ) -> ImportJob | None:
        current = self.get(job_id)
        if current is None:
            return None

        if status is not None:
            validate_status_transition(current.status, status)
        if stage is not None:
            validate_stage_transition(current.stage, stage)

        assignments: list[str] = ["updated_at = %s"]
        params: list[Any] = [datetime.now(UTC)]

        if status is not None:
            assignments.append("status = %s")
            params.append(status.value)
        if progress is not None:
            if progress < 0 or progress > 100:
                raise ValueError("progress 必须在 0-100 之间。")
            if status not in (
                ImportJobStatus.FAILED,
                ImportJobStatus.CANCELLED,
            ):
                progress = max(current.progress, progress)
            assignments.append("progress = %s")
            params.append(progress)
        if stage is not None:
            assignments.append("stage = %s")
            params.append(stage.value)
        if clear_error:
            assignments.extend(["error_code = NULL", "error_message = NULL"])
        else:
            if error_code is not None:
                assignments.append("error_code = %s")
                params.append(error_code)
            if error_message is not None:
                assignments.append("error_message = %s")
                params.append(sanitize_pg_text(error_message))

        params.append(job_id)
        sql = f"""
            UPDATE app.t_fact_import_job
            SET {", ".join(assignments)}
            WHERE id = %s
            RETURNING id
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                updated = cur.fetchone() is not None
            self._commit(conn)

        if not updated:
            return None
        return self.get(job_id)

    def cancel(self, job_id: str) -> ImportJob | None:
        current = self.get(job_id)
        if current is None:
            return None
        if current.status not in CANCELLABLE_STATUSES:
            raise ImportJobTransitionError(
                current_status=current.status,
                target_status=ImportJobStatus.CANCELLED,
                message=f"导入任务 {job_id} 当前状态 {current.status.value} 不可取消",
            )

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.t_fact_import_job
                    SET status = %s, updated_at = %s
                    WHERE id = %s
                      AND status = ANY(%s)
                    RETURNING id
                    """,
                    (
                        ImportJobStatus.CANCELLED.value,
                        datetime.now(UTC),
                        job_id,
                        [status.value for status in CANCELLABLE_STATUSES],
                    ),
                )
                updated = cur.fetchone() is not None
            self._commit(conn)

        if not updated:
            return None
        return self.get(job_id)

    def count_active_by_kb(self, kb_id: str) -> int:
        active_statuses = (
            ImportJobStatus.QUEUED.value,
            ImportJobStatus.RUNNING.value,
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM app.t_fact_import_job
                    WHERE kb_id = %s
                      AND status = ANY(%s)
                    """,
                    (kb_id, list(active_statuses)),
                )
                row = cur.fetchone() or {"total": 0}
                return int(row["total"])

    def list_by_kb(
        self,
        kb_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ImportJob]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_fact_import_job
                    WHERE kb_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (kb_id, limit, offset),
                )
                rows = cur.fetchall() or []
                jobs: list[ImportJob] = []
                for row in rows:
                    job_id = str(row["id"])
                    file_ids = self._fetch_file_ids(cur, job_id)
                    option = self._fetch_option(cur, job_id)
                    jobs.append(
                        ImportJob.from_row(dict(row), file_ids=file_ids, option=option)
                    )
                return jobs

    @staticmethod
    def _fetch_file_ids(cur: psycopg.Cursor, job_id: str) -> list[str]:
        cur.execute(
            """
            SELECT file_id
            FROM app.t_fact_import_job_file
            WHERE import_job_id = %s
            ORDER BY created_at ASC, file_id ASC
            """,
            (job_id,),
        )
        return [str(row["file_id"]) for row in (cur.fetchall() or [])]

    @staticmethod
    def _fetch_option(cur: psycopg.Cursor, job_id: str) -> ImportJobOption | None:
        cur.execute(
            """
            SELECT chunk_strategy, meta_filename, meta_headings
            FROM app.t_fact_import_job_option
            WHERE import_job_id = %s
            """,
            (job_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return ImportJobOption(
            chunk_strategy=str(row["chunk_strategy"]),
            meta_filename=bool(row["meta_filename"]),
            meta_headings=bool(row["meta_headings"]),
        )

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
