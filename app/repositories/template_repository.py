from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from app.core.database import normalize_psycopg_url
from app.domain.template import Template, TemplateScope, build_snippet, new_template_id


class TemplateNotFoundError(LookupError):
    def __init__(self, template_id: str) -> None:
        super().__init__(f"Template not found: {template_id}")
        self.template_id = template_id


class TemplateNameDuplicatedError(ValueError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Template name already exists: {name}")
        self.name = name


class TemplateRepository:
    """聊天模板 PostgreSQL 仓储，使用原生 SQL 与 DATABASE_URL 兼容。"""

    def __init__(
        self,
        database_url: str,
        *,
        connection: psycopg.Connection | None = None,
    ) -> None:
        if not database_url.strip():
            raise ValueError("TemplateRepository 需要配置 DATABASE_URL。")
        self._dsn = normalize_psycopg_url(database_url)
        self._external_conn = connection

    def ensure_schema(self) -> None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS app")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_dim_chat_template (
                        id text PRIMARY KEY,
                        name text NOT NULL,
                        content text NOT NULL,
                        snippet text NOT NULL DEFAULT '',
                        scope text NOT NULL DEFAULT 'personal',
                        owner_user_id text NOT NULL,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now(),
                        UNIQUE (owner_user_id, name)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_dim_chat_template_owner_user_id_idx
                    ON app.t_dim_chat_template (owner_user_id)
                    """
                )

    def list_by_owner(self, owner_user_id: str) -> list[Template]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_chat_template
                    WHERE owner_user_id = %s
                    ORDER BY updated_at DESC, name ASC
                    """,
                    (owner_user_id,),
                )
                rows = cur.fetchall()
        return [Template.from_row(dict(row)) for row in rows]

    def get_by_id(self, template_id: str) -> Template | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_chat_template
                    WHERE id = %s
                    """,
                    (template_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return Template.from_row(dict(row))

    def create(
        self,
        *,
        owner_user_id: str,
        name: str,
        content: str,
        snippet: str | None = None,
        scope: TemplateScope = TemplateScope.PERSONAL,
        template_id: str | None = None,
    ) -> Template:
        template_id = template_id or new_template_id()
        resolved_snippet = snippet if snippet is not None else build_snippet(content)
        now = datetime.now(UTC)

        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO app.t_dim_chat_template (
                            id, name, content, snippet, scope, owner_user_id,
                            created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            template_id,
                            name,
                            content,
                            resolved_snippet,
                            scope.value,
                            owner_user_id,
                            now,
                            now,
                        ),
                    )
                    row = cur.fetchone()
                self._commit(conn)
        except UniqueViolation as exc:
            raise TemplateNameDuplicatedError(name) from exc

        return Template.from_row(dict(row))

    def update(
        self,
        template_id: str,
        *,
        name: str | None = None,
        content: str | None = None,
        snippet: str | None = None,
        scope: TemplateScope | None = None,
    ) -> Template:
        existing = self.get_by_id(template_id)
        if existing is None:
            raise TemplateNotFoundError(template_id)

        resolved_name = name if name is not None else existing.name
        resolved_content = content if content is not None else existing.content
        if snippet is not None:
            resolved_snippet = snippet
        elif content is not None:
            resolved_snippet = build_snippet(resolved_content)
        else:
            resolved_snippet = existing.snippet
        resolved_scope = scope if scope is not None else existing.scope
        now = datetime.now(UTC)

        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE app.t_dim_chat_template
                        SET name = %s,
                            content = %s,
                            snippet = %s,
                            scope = %s,
                            updated_at = %s
                        WHERE id = %s
                        RETURNING *
                        """,
                        (
                            resolved_name,
                            resolved_content,
                            resolved_snippet,
                            resolved_scope.value,
                            now,
                            template_id,
                        ),
                    )
                    row = cur.fetchone()
                self._commit(conn)
        except UniqueViolation as exc:
            raise TemplateNameDuplicatedError(resolved_name) from exc

        if row is None:
            raise TemplateNotFoundError(template_id)
        return Template.from_row(dict(row))

    def delete(self, template_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM app.t_dim_chat_template
                    WHERE id = %s
                    RETURNING id
                    """,
                    (template_id,),
                )
                row = cur.fetchone()
            self._commit(conn)

        if row is None:
            raise TemplateNotFoundError(template_id)

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
