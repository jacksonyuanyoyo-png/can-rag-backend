from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator
import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from app.core.database import normalize_psycopg_url
from app.domain.folder import Folder, new_folder_id


class FolderNotFoundError(LookupError):
    pass


class FolderNameDuplicatedError(ValueError):
    pass


class FolderRepository:
    """会话文件夹 PostgreSQL 仓储，使用原生 SQL 与 DATABASE_URL 兼容。"""

    def __init__(
        self,
        database_url: str,
        *,
        connection: psycopg.Connection | None = None,
    ) -> None:
        if not database_url.strip():
            raise ValueError("FolderRepository 需要配置 DATABASE_URL。")
        self._dsn = normalize_psycopg_url(database_url)
        self._external_conn = connection

    def ensure_schema(self) -> None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS app")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_dim_folder (
                        id text PRIMARY KEY,
                        name text NOT NULL,
                        owner_user_id text NOT NULL,
                        team_id text,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS t_dim_folder_owner_scope_name_idx
                    ON app.t_dim_folder (
                        owner_user_id,
                        COALESCE(team_id, ''),
                        name
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_dim_folder_owner_user_id_idx
                    ON app.t_dim_folder (owner_user_id)
                    """
                )

    def list_by_owner(
        self,
        owner_user_id: str,
        *,
        team_id: str | None = None,
    ) -> list[Folder]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if team_id is None:
                    cur.execute(
                        """
                        SELECT *
                        FROM app.t_dim_folder
                        WHERE owner_user_id = %s
                          AND team_id IS NULL
                        ORDER BY name ASC, created_at ASC
                        """,
                        (owner_user_id,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT *
                        FROM app.t_dim_folder
                        WHERE owner_user_id = %s
                          AND team_id = %s
                        ORDER BY name ASC, created_at ASC
                        """,
                        (owner_user_id, team_id),
                    )
                rows = cur.fetchall() or []
                return [Folder.from_row(dict(row)) for row in rows]

    def create(
        self,
        *,
        name: str,
        owner_user_id: str,
        team_id: str | None = None,
        folder_id: str | None = None,
    ) -> Folder:
        folder_id = folder_id or new_folder_id()
        now = datetime.now(UTC)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SAVEPOINT folder_create")
                try:
                    cur.execute(
                        """
                        INSERT INTO app.t_dim_folder (
                            id, name, owner_user_id, team_id, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (folder_id, name, owner_user_id, team_id, now, now),
                    )
                    row = cur.fetchone()
                except UniqueViolation as exc:
                    conn.execute("ROLLBACK TO SAVEPOINT folder_create")
                    raise FolderNameDuplicatedError(
                        f"文件夹名称已存在: {name}"
                    ) from exc
                self._commit(conn)

        if row is None:
            raise RuntimeError("创建文件夹失败。")
        return Folder.from_row(dict(row))

    def get(self, folder_id: str) -> Folder | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_folder
                    WHERE id = %s
                    """,
                    (folder_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return Folder.from_row(dict(row))

    def get_for_owner(self, folder_id: str, owner_user_id: str) -> Folder | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_folder
                    WHERE id = %s AND owner_user_id = %s
                    """,
                    (folder_id, owner_user_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return Folder.from_row(dict(row))

    def require_for_owner(self, folder_id: str, owner_user_id: str) -> Folder:
        folder = self.get_for_owner(folder_id, owner_user_id)
        if folder is None:
            raise FolderNotFoundError(f"文件夹不存在: {folder_id}")
        return folder

    def update(
        self,
        folder_id: str,
        *,
        owner_user_id: str,
        name: str,
    ) -> Folder:
        now = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SAVEPOINT folder_update")
                try:
                    cur.execute(
                        """
                        UPDATE app.t_dim_folder
                        SET name = %s, updated_at = %s
                        WHERE id = %s AND owner_user_id = %s
                        RETURNING *
                        """,
                        (name, now, folder_id, owner_user_id),
                    )
                    row = cur.fetchone()
                except UniqueViolation as exc:
                    conn.execute("ROLLBACK TO SAVEPOINT folder_update")
                    raise FolderNameDuplicatedError(
                        f"文件夹名称已存在: {name}"
                    ) from exc
                self._commit(conn)

        if row is None:
            raise FolderNotFoundError(f"文件夹不存在: {folder_id}")
        return Folder.from_row(dict(row))

    def delete(self, folder_id: str, *, owner_user_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM app.t_dim_folder
                    WHERE id = %s AND owner_user_id = %s
                    """,
                    (folder_id, owner_user_id),
                )
                deleted = cur.rowcount > 0
            self._commit(conn)

        if not deleted:
            raise FolderNotFoundError(f"文件夹不存在: {folder_id}")

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
