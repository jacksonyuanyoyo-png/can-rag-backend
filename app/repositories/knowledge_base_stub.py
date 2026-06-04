from __future__ import annotations

from datetime import UTC, datetime

import psycopg


def resolve_knowledge_base_stub_name(
    cur: psycopg.Cursor,
    kb_id: str,
    name: str | None = None,
) -> str:
    """Return a display name that satisfies t_dim_knowledge_base_name_uq."""
    display_name = (name or kb_id).strip() or kb_id
    cur.execute(
        """
        SELECT 1 FROM app.t_dim_knowledge_base
        WHERE name = %s AND id <> %s
        LIMIT 1
        """,
        (display_name, kb_id),
    )
    if cur.fetchone():
        return kb_id
    return display_name


def insert_knowledge_base_stub(
    cur: psycopg.Cursor,
    kb_id: str,
    *,
    name: str | None = None,
) -> None:
    display_name = resolve_knowledge_base_stub_name(cur, kb_id, name)
    now = datetime.now(UTC)
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
