from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.core.database import normalize_psycopg_url
from app.domain.idempotency import (
    IdempotencyAcquireOutcome,
    IdempotencyAcquireResult,
    IdempotencyRecord,
)


class IdempotencyRepository:
    """幂等键 PostgreSQL 仓储，使用原生 SQL 与 DATABASE_URL 兼容。"""

    def __init__(
        self,
        database_url: str,
        *,
        connection: psycopg.Connection | None = None,
    ) -> None:
        if not database_url.strip():
            raise ValueError("IdempotencyRepository 需要配置 DATABASE_URL。")
        self._dsn = normalize_psycopg_url(database_url)
        self._external_conn = connection

    def ensure_schema(self) -> None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS app")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_fact_idempotency_key (
                        id text PRIMARY KEY,
                        user_id text NOT NULL,
                        idempotency_key text NOT NULL,
                        request_hash text NOT NULL,
                        response_status integer,
                        response_body jsonb,
                        expires_at timestamptz NOT NULL,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        UNIQUE (user_id, idempotency_key)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_idempotency_key_expires_at_idx
                    ON app.t_fact_idempotency_key (expires_at)
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE app.t_fact_idempotency_key
                    ADD COLUMN IF NOT EXISTS response_status integer
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE app.t_fact_idempotency_key
                    ADD COLUMN IF NOT EXISTS response_body jsonb
                    """
                )

    def acquire(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        request_hash: str,
        ttl_seconds: int = 86400,
        record_id: str | None = None,
    ) -> IdempotencyAcquireOutcome:
        record_id = record_id or f"idem_{uuid4().hex}"
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_fact_idempotency_key (
                        id, user_id, idempotency_key, request_hash, expires_at
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, idempotency_key) DO NOTHING
                    RETURNING *
                    """,
                    (record_id, user_id, idempotency_key, request_hash, expires_at),
                )
                inserted = cur.fetchone()
                if inserted is not None:
                    self._commit(conn)
                    return IdempotencyAcquireOutcome(
                        result=IdempotencyAcquireResult.ACQUIRED,
                        record=IdempotencyRecord.from_row(dict(inserted)),
                    )

                existing = self._select_for_update(cur, user_id, idempotency_key)
                if existing is None:
                    self._commit(conn)
                    return IdempotencyAcquireOutcome(
                        result=IdempotencyAcquireResult.IN_PROGRESS,
                    )

                record = IdempotencyRecord.from_row(existing)
                if record.request_hash != request_hash:
                    self._commit(conn)
                    return IdempotencyAcquireOutcome(
                        result=IdempotencyAcquireResult.CONFLICT,
                        record=record,
                    )
                if record.response_status is not None:
                    self._commit(conn)
                    return IdempotencyAcquireOutcome(
                        result=IdempotencyAcquireResult.REPLAY,
                        record=record,
                    )

                self._commit(conn)
                return IdempotencyAcquireOutcome(
                    result=IdempotencyAcquireResult.IN_PROGRESS,
                    record=record,
                )

    def complete(
        self,
        record_id: str,
        *,
        response_status: int,
        response_body: dict[str, Any],
    ) -> IdempotencyRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.t_fact_idempotency_key
                    SET response_status = %s,
                        response_body = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (response_status, Json(response_body), record_id),
                )
                row = cur.fetchone()
            self._commit(conn)

        if row is None:
            return None
        return IdempotencyRecord.from_row(dict(row))

    def get(self, user_id: str, idempotency_key: str) -> IdempotencyRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_fact_idempotency_key
                    WHERE user_id = %s
                      AND idempotency_key = %s
                    """,
                    (user_id, idempotency_key),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return IdempotencyRecord.from_row(dict(row))

    def delete_expired(self, *, before: datetime | None = None) -> int:
        cutoff = before or datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM app.t_fact_idempotency_key
                    WHERE expires_at < %s
                    """,
                    (cutoff,),
                )
                deleted = cur.rowcount
            self._commit(conn)
        return int(deleted)

    def _commit(self, conn: psycopg.Connection) -> None:
        if self._external_conn is None:
            conn.commit()

    @staticmethod
    def _select_for_update(
        cur: psycopg.Cursor,
        user_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        cur.execute(
            """
            SELECT *
            FROM app.t_fact_idempotency_key
            WHERE user_id = %s
              AND idempotency_key = %s
            FOR UPDATE
            """,
            (user_id, idempotency_key),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None

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
