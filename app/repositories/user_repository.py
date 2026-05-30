from __future__ import annotations

import hashlib
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from app.core.database import normalize_psycopg_url
from app.domain.user import User


class UserNotFoundError(LookupError):
    def __init__(self, user_id: str) -> None:
        super().__init__(f"User not found: {user_id}")
        self.user_id = user_id


class UserEmailDuplicatedError(ValueError):
    def __init__(self, email: str) -> None:
        super().__init__(f"User email already exists: {email}")
        self.email = email


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"sha256${salt}${digest}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    if stored_hash.startswith("plain:"):
        return secrets.compare_digest(stored_hash, f"plain:{password}")
    if stored_hash.startswith("sha256$"):
        try:
            _, salt, digest = stored_hash.split("$", 2)
        except ValueError:
            return False
        candidate = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
        return secrets.compare_digest(candidate, digest)
    return False


class UserRepository:
    """用户 PostgreSQL 仓储，使用原生 SQL 与 DATABASE_URL 兼容。"""

    def __init__(
        self,
        database_url: str,
        *,
        connection: psycopg.Connection | None = None,
    ) -> None:
        if not database_url.strip():
            raise ValueError("UserRepository 需要配置 DATABASE_URL。")
        self._dsn = normalize_psycopg_url(database_url)
        self._external_conn = connection

    def ensure_schema(self) -> None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS app")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_dim_user (
                        id              text PRIMARY KEY,
                        email           text NOT NULL,
                        display_name    text NOT NULL,
                        password_hash   text,
                        status          text NOT NULL DEFAULT 'active',
                        default_team_id text,
                        created_at      timestamptz NOT NULL DEFAULT now(),
                        updated_at      timestamptz NOT NULL DEFAULT now(),
                        CONSTRAINT t_dim_user_status_chk
                            CHECK (status IN ('active', 'inactive', 'suspended'))
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS t_dim_user_email_uq
                    ON app.t_dim_user (lower(email))
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_dim_role (
                        id          text PRIMARY KEY,
                        code        text NOT NULL,
                        name        text NOT NULL,
                        scope       text NOT NULL DEFAULT 'team',
                        created_at  timestamptz NOT NULL DEFAULT now(),
                        updated_at  timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS t_dim_role_code_uq
                    ON app.t_dim_role (code)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_dim_permission (
                        id          text PRIMARY KEY,
                        code        text NOT NULL,
                        domain      text NOT NULL,
                        description text,
                        created_at  timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS t_dim_permission_code_uq
                    ON app.t_dim_permission (code)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_dim_role_permission (
                        role_id         text NOT NULL REFERENCES app.t_dim_role (id) ON DELETE CASCADE,
                        permission_id   text NOT NULL REFERENCES app.t_dim_permission (id) ON DELETE CASCADE,
                        created_at      timestamptz NOT NULL DEFAULT now(),
                        PRIMARY KEY (role_id, permission_id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_fact_user_team (
                        id              text PRIMARY KEY,
                        user_id         text NOT NULL REFERENCES app.t_dim_user (id) ON DELETE CASCADE,
                        team_id         text NOT NULL,
                        role_in_team    text,
                        created_at      timestamptz NOT NULL DEFAULT now(),
                        updated_at      timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS t_fact_user_team_user_team_uq
                    ON app.t_fact_user_team (user_id, team_id)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_fact_user_role (
                        id          text PRIMARY KEY,
                        user_id     text NOT NULL REFERENCES app.t_dim_user (id) ON DELETE CASCADE,
                        role_id     text NOT NULL REFERENCES app.t_dim_role (id) ON DELETE CASCADE,
                        team_id     text,
                        granted_at  timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_user_role_user_id_idx
                    ON app.t_fact_user_role (user_id)
                    """
                )

    def find_by_id(self, user_id: str) -> User | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_user
                    WHERE id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                if str(row.get("status") or "active") != "active":
                    return None
                team_id = self._resolve_team_id(cur, user_id, row.get("default_team_id"))
                permissions = self._load_permissions(cur, user_id, team_id or None)
        return User.from_row(dict(row), permissions=permissions, team_id=team_id)

    def find_by_email(self, email: str) -> User | None:
        normalized = email.strip().lower()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_user
                    WHERE lower(email) = %s
                    """,
                    (normalized,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                if str(row.get("status") or "active") != "active":
                    return None
                user_id = str(row["id"])
                team_id = self._resolve_team_id(cur, user_id, row.get("default_team_id"))
                permissions = self._load_permissions(cur, user_id, team_id or None)
        return User.from_row(dict(row), permissions=permissions, team_id=team_id)

    def verify_credentials(self, email: str, password: str) -> User | None:
        normalized = email.strip().lower()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_user
                    WHERE lower(email) = %s
                    """,
                    (normalized,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                if str(row.get("status") or "active") != "active":
                    return None
                if not verify_password(password, row.get("password_hash")):
                    return None
                user_id = str(row["id"])
                team_id = self._resolve_team_id(cur, user_id, row.get("default_team_id"))
                permissions = self._load_permissions(cur, user_id, team_id or None)
        return User.from_row(dict(row), permissions=permissions, team_id=team_id)

    def create_user(
        self,
        *,
        user_id: str,
        email: str,
        display_name: str,
        password: str | None = None,
        password_hash: str | None = None,
        default_team_id: str | None = None,
        status: str = "active",
    ) -> User:
        normalized_email = email.strip()
        resolved_hash = password_hash
        if resolved_hash is None and password is not None:
            resolved_hash = hash_password(password)
        now = datetime.now(UTC)

        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO app.t_dim_user (
                            id, email, display_name, password_hash,
                            status, default_team_id, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            user_id,
                            normalized_email,
                            display_name,
                            resolved_hash,
                            status,
                            default_team_id,
                            now,
                            now,
                        ),
                    )
                    row = cur.fetchone()
                self._commit(conn)
        except UniqueViolation as exc:
            raise UserEmailDuplicatedError(normalized_email) from exc

        return User.from_row(dict(row), permissions=[], team_id=default_team_id or "")

    def grant_role(
        self,
        *,
        user_id: str,
        role_id: str,
        team_id: str | None = None,
        grant_id: str | None = None,
    ) -> None:
        grant_id = grant_id or f"ur_{secrets.token_hex(8)}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_fact_user_role (id, user_id, role_id, team_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (grant_id, user_id, role_id, team_id),
                )
            self._commit(conn)

    def upsert_permission(self, *, permission_id: str, code: str, domain: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_dim_permission (id, code, domain)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                    SET code = EXCLUDED.code,
                        domain = EXCLUDED.domain
                    """,
                    (permission_id, code, domain),
                )
            self._commit(conn)

    def upsert_role(self, *, role_id: str, code: str, name: str, scope: str = "team") -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_dim_role (id, code, name, scope)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                    SET code = EXCLUDED.code,
                        name = EXCLUDED.name,
                        scope = EXCLUDED.scope
                    """,
                    (role_id, code, name, scope),
                )
            self._commit(conn)

    def link_role_permission(self, *, role_id: str, permission_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_dim_role_permission (role_id, permission_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (role_id, permission_id),
                )
            self._commit(conn)

    def _load_permissions(
        self,
        cur: psycopg.Cursor,
        user_id: str,
        team_id: str | None,
    ) -> list[str]:
        if team_id:
            cur.execute(
                """
                SELECT DISTINCT p.code
                FROM app.t_fact_user_role ur
                JOIN app.t_dim_role_permission rp ON rp.role_id = ur.role_id
                JOIN app.t_dim_permission p ON p.id = rp.permission_id
                WHERE ur.user_id = %s
                  AND (ur.team_id IS NULL OR ur.team_id = %s)
                ORDER BY p.code
                """,
                (user_id, team_id),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT p.code
                FROM app.t_fact_user_role ur
                JOIN app.t_dim_role_permission rp ON rp.role_id = ur.role_id
                JOIN app.t_dim_permission p ON p.id = rp.permission_id
                WHERE ur.user_id = %s
                  AND ur.team_id IS NULL
                ORDER BY p.code
                """,
                (user_id,),
            )
        rows = cur.fetchall()
        return [str(row["code"]) for row in rows]

    def _resolve_team_id(
        self,
        cur: psycopg.Cursor,
        user_id: str,
        default_team_id: str | None,
    ) -> str:
        if default_team_id:
            return str(default_team_id)
        cur.execute(
            """
            SELECT team_id
            FROM app.t_fact_user_team
            WHERE user_id = %s
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if row and row.get("team_id") is not None:
            return str(row["team_id"])
        return ""

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
