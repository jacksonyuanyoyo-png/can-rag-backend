from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Iterator

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from app.core.database import normalize_psycopg_url
from app.domain.model import Model
from app.domain.model_catalog import LEGACY_PLACEHOLDER_MODEL_IDS, ModelCatalogEntry


class ModelNotFoundError(LookupError):
    def __init__(self, model_id: str) -> None:
        super().__init__(f"Model not found: {model_id}")
        self.model_id = model_id


class ModelCodeDuplicatedError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(f"Model code already exists: {code}")
        self.code = code


class ModelRepository:
    """LLM 模型 PostgreSQL 仓储，使用原生 SQL 与 DATABASE_URL 兼容。"""

    def __init__(
        self,
        database_url: str,
        *,
        connection: psycopg.Connection | None = None,
    ) -> None:
        if not database_url.strip():
            raise ValueError("ModelRepository 需要配置 DATABASE_URL。")
        self._dsn = normalize_psycopg_url(database_url)
        self._external_conn = connection

    def ensure_schema(self) -> None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS app")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_dim_model (
                        id              text PRIMARY KEY,
                        code            text NOT NULL,
                        display_name    text NOT NULL,
                        provider        text,
                        icon            text,
                        status          text NOT NULL DEFAULT 'active',
                        visibility      text NOT NULL DEFAULT 'system',
                        created_at      timestamptz NOT NULL DEFAULT now(),
                        updated_at      timestamptz NOT NULL DEFAULT now(),
                        CONSTRAINT t_dim_model_status_chk
                            CHECK (status IN ('active', 'inactive', 'deprecated')),
                        CONSTRAINT t_dim_model_visibility_chk
                            CHECK (visibility IN ('system', 'team', 'personal'))
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS t_dim_model_code_uq
                    ON app.t_dim_model (code)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_dim_model_status_idx
                    ON app.t_dim_model (status)
                    """
                )

    def sync_openai_catalog(self, entries: list[ModelCatalogEntry]) -> None:
        """将 OpenAI 模型目录 upsert 到 app.t_dim_model，并停用历史占位项。"""
        now = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                for entry in entries:
                    cur.execute(
                        """
                        INSERT INTO app.t_dim_model (
                            id, code, display_name, provider, icon,
                            status, visibility, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            code = EXCLUDED.code,
                            display_name = EXCLUDED.display_name,
                            provider = EXCLUDED.provider,
                            icon = EXCLUDED.icon,
                            status = EXCLUDED.status,
                            visibility = EXCLUDED.visibility,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (
                            entry.id,
                            entry.code,
                            entry.display_name,
                            entry.provider,
                            entry.icon,
                            entry.status,
                            entry.visibility,
                            now,
                            now,
                        ),
                    )
                if LEGACY_PLACEHOLDER_MODEL_IDS:
                    cur.execute(
                        """
                        UPDATE app.t_dim_model
                        SET status = 'inactive', updated_at = %s
                        WHERE id = ANY(%s)
                        """,
                        (now, list(LEGACY_PLACEHOLDER_MODEL_IDS)),
                    )
            self._commit(conn)

    def list_active(self) -> list[Model]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_model
                    WHERE status = 'active'
                    ORDER BY display_name ASC, code ASC
                    """
                )
                rows = cur.fetchall()
        return [Model.from_row(dict(row)) for row in rows]

    def get_by_id(self, model_id: str) -> Model | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_model
                    WHERE id = %s
                    """,
                    (model_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return Model.from_row(dict(row))

    def create(
        self,
        *,
        model_id: str,
        code: str,
        display_name: str,
        icon: str | None = None,
        provider: str | None = None,
        status: str = "active",
        visibility: str = "system",
    ) -> Model:
        now = datetime.now(UTC)
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO app.t_dim_model (
                            id, code, display_name, provider, icon,
                            status, visibility, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            model_id,
                            code,
                            display_name,
                            provider,
                            icon,
                            status,
                            visibility,
                            now,
                            now,
                        ),
                    )
                    row = cur.fetchone()
                self._commit(conn)
        except UniqueViolation as exc:
            raise ModelCodeDuplicatedError(code) from exc
        return Model.from_row(dict(row))

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
