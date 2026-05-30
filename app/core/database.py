from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.config import Settings


BOOTSTRAP_MIGRATION = "0001_database_bootstrap"


def normalize_psycopg_url(database_url: str) -> str:
    """Convert SQLAlchemy-style URLs to psycopg-compatible DSNs."""
    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    if database_url.startswith("postgres+psycopg://"):
        return database_url.replace("postgres+psycopg://", "postgresql://", 1)
    return database_url


def is_database_configured(settings: Settings) -> bool:
    return bool(settings.DATABASE_URL.strip())


def initialize_database(settings: Settings) -> dict[str, Any]:
    """Initialize PostgreSQL objects needed by the application.

    This function is safe to run on every startup.
    """
    if not is_database_configured(settings):
        return {"configured": False, "status": "disabled"}

    dsn = normalize_psycopg_url(settings.DATABASE_URL)
    try:
        with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute("CREATE SCHEMA IF NOT EXISTS app")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.schema_migrations (
                        version text PRIMARY KEY,
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    INSERT INTO app.schema_migrations (version)
                    VALUES (%s)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    (BOOTSTRAP_MIGRATION,),
                )
    except Exception as exc:
        return {
            "configured": True,
            "status": "error",
            "error": str(exc),
        }

    return check_database(settings)


def check_database(settings: Settings) -> dict[str, Any]:
    if not is_database_configured(settings):
        return {"configured": False, "status": "disabled"}

    dsn = normalize_psycopg_url(settings.DATABASE_URL)
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        current_database() AS database,
                        current_user AS user_name,
                        version() AS postgres_version
                    """
                )
                database_info = dict(cur.fetchone() or {})

                cur.execute(
                    """
                    SELECT extversion
                    FROM pg_extension
                    WHERE extname = 'vector'
                    """
                )
                extension = cur.fetchone()

                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'app'
                          AND table_name = 'schema_migrations'
                    ) AS schema_ready
                    """
                )
                schema = cur.fetchone() or {}

        return {
            "configured": True,
            "status": "ok",
            "database": database_info.get("database"),
            "user": database_info.get("user_name"),
            "postgres_version": database_info.get("postgres_version"),
            "pgvector": {
                "enabled": extension is not None,
                "version": extension["extversion"] if extension else None,
            },
            "schema_ready": bool(schema.get("schema_ready")),
        }
    except Exception as exc:
        return {
            "configured": True,
            "status": "error",
            "error": str(exc),
        }
