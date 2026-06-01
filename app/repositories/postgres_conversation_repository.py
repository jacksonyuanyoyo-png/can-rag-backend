from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from app.core.database import normalize_psycopg_url
from app.domain.conversation import (
    ConversationRecord,
    ConversationStatus,
    MessageRecord,
    MessageRole,
    MessageStatus,
    MessageUsage,
)
from app.repositories.conversation_repository import ConversationNotFoundError


class MessageNotFoundInRepositoryError(LookupError):
    def __init__(self, message_id: str) -> None:
        super().__init__(f"Message not found: {message_id}")
        self.message_id = message_id


def _parse_created_at(value: str | datetime | None, *, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return fallback


def _iso_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    return str(value)


def _new_conversation_id() -> str:
    return f"conv_{uuid.uuid4().hex[:12]}"


class PostgresConversationRepository:
    """会话 PostgreSQL 仓储：持久化多轮消息与引用。"""

    def __init__(
        self,
        database_url: str,
        *,
        connection: psycopg.Connection | None = None,
    ) -> None:
        if not database_url.strip():
            raise ValueError("PostgresConversationRepository 需要配置 DATABASE_URL。")
        self._dsn = normalize_psycopg_url(database_url)
        self._external_conn = connection

    def ensure_schema(self) -> None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS app")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_dim_conversation (
                        id                  text PRIMARY KEY,
                        title               text NOT NULL DEFAULT 'New chat',
                        owner_user_id       text,
                        folder_id           text,
                        status              text NOT NULL DEFAULT 'active',
                        pinned              boolean NOT NULL DEFAULT false,
                        last_message_at     timestamptz,
                        created_at          timestamptz NOT NULL DEFAULT now(),
                        updated_at          timestamptz NOT NULL DEFAULT now(),
                        CONSTRAINT t_dim_conversation_status_chk
                            CHECK (status IN ('active', 'archived', 'deleted'))
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_fact_message (
                        id                  text PRIMARY KEY,
                        conversation_id     text NOT NULL
                            REFERENCES app.t_dim_conversation (id) ON DELETE CASCADE,
                        role                text NOT NULL,
                        content             text NOT NULL DEFAULT '',
                        status              text NOT NULL DEFAULT 'completed',
                        model_id            text,
                        edited_at           timestamptz,
                        citations_json      jsonb,
                        sources_json        jsonb,
                        created_at          timestamptz NOT NULL DEFAULT now(),
                        updated_at          timestamptz NOT NULL DEFAULT now(),
                        CONSTRAINT t_fact_message_role_chk
                            CHECK (role IN ('user', 'assistant', 'system', 'tool')),
                        CONSTRAINT t_fact_message_status_chk
                            CHECK (status IN (
                                'pending', 'streaming', 'completed', 'failed', 'cancelled'
                            ))
                    )
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE app.t_fact_message
                    ADD COLUMN IF NOT EXISTS citations_json jsonb
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE app.t_fact_message
                    ADD COLUMN IF NOT EXISTS sources_json jsonb
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS t_fact_message_conversation_created_idx
                    ON app.t_fact_message (conversation_id, created_at)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_fact_message_usage (
                        id                  text PRIMARY KEY,
                        message_id          text NOT NULL UNIQUE
                            REFERENCES app.t_fact_message (id) ON DELETE CASCADE,
                        prompt_tokens       integer NOT NULL DEFAULT 0,
                        completion_tokens   integer NOT NULL DEFAULT 0,
                        total_tokens        integer NOT NULL DEFAULT 0,
                        created_at          timestamptz NOT NULL DEFAULT now(),
                        CONSTRAINT t_fact_message_usage_tokens_chk
                            CHECK (
                                prompt_tokens >= 0
                                AND completion_tokens >= 0
                                AND total_tokens >= 0
                            )
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_fact_conversation_kb (
                        id                  text PRIMARY KEY,
                        conversation_id     text NOT NULL
                            REFERENCES app.t_dim_conversation (id) ON DELETE CASCADE,
                        kb_id               text NOT NULL,
                        is_active           boolean NOT NULL DEFAULT true,
                        created_at          timestamptz NOT NULL DEFAULT now(),
                        updated_at          timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS t_fact_conversation_kb_conv_kb_uq
                    ON app.t_fact_conversation_kb (conversation_id, kb_id)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.t_fact_message_feedback (
                        id          text PRIMARY KEY,
                        message_id  text NOT NULL
                            REFERENCES app.t_fact_message (id) ON DELETE CASCADE,
                        user_id     text NOT NULL,
                        rating      text NOT NULL,
                        comment     text,
                        created_at  timestamptz NOT NULL DEFAULT now(),
                        CONSTRAINT t_fact_message_feedback_rating_chk
                            CHECK (rating IN ('positive', 'negative'))
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS t_fact_message_feedback_message_user_uq
                    ON app.t_fact_message_feedback (message_id, user_id)
                    """
                )

    def list_all(self) -> list[ConversationRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM app.t_dim_conversation
                    WHERE status <> 'deleted'
                    ORDER BY updated_at DESC
                    """
                )
                rows = cur.fetchall() or []

            conversations: list[ConversationRecord] = []
            for row in rows:
                conversation_id = str(row["id"])
                messages = self._fetch_messages(conn, conversation_id)
                kb_ids = self._fetch_kb_ids(conn, conversation_id)
                record = self._conversation_from_row(dict(row), messages=messages)
                record.knowledge_base_ids = kb_ids
                conversations.append(record)
        return conversations

    def get(self, conversation_id: str) -> ConversationRecord | None:
        try:
            return self.require(conversation_id)
        except ConversationNotFoundError:
            return None

    def require(self, conversation_id: str) -> ConversationRecord:
        with self._connect() as conn:
            conversation_row = self._fetch_conversation_row(conn, conversation_id)
            if conversation_row is None:
                raise ConversationNotFoundError(conversation_id)
            messages = self._fetch_messages(conn, conversation_id)
            kb_ids = self._fetch_kb_ids(conn, conversation_id)

        record = self._conversation_from_row(conversation_row, messages=messages)
        record.knowledge_base_ids = kb_ids
        return record

    def create(
        self,
        *,
        title: str,
        folder: str | None = None,
        pinned: bool = False,
    ) -> ConversationRecord:
        conversation_id = _new_conversation_id()
        now = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_dim_conversation (
                        id, title, folder_id, status, pinned, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, 'active', %s, %s, %s)
                    RETURNING *
                    """,
                    (conversation_id, title, folder, pinned, now, now),
                )
                row = dict(cur.fetchone())
            self._commit(conn)
        return self._conversation_from_row(row, messages=[])

    def add_messages(
        self,
        conversation_id: str,
        messages: list[MessageRecord],
    ) -> ConversationRecord:
        if not messages:
            return self.require(conversation_id)

        now = datetime.now(UTC)
        with self._connect() as conn:
            if self._fetch_conversation_row(conn, conversation_id) is None:
                raise ConversationNotFoundError(conversation_id)

            with conn.cursor() as cur:
                for message in messages:
                    cur.execute(
                        """
                        INSERT INTO app.t_fact_message (
                            id,
                            conversation_id,
                            role,
                            content,
                            status,
                            citations_json,
                            sources_json,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            message.id,
                            conversation_id,
                            message.role.value,
                            message.content,
                            message.status.value,
                            Json(message.citations) if message.citations else None,
                            Json(message.sources) if message.sources else None,
                            _parse_created_at(message.created_at, fallback=now),
                            now,
                        ),
                    )
                    if message.usage is not None:
                        self._upsert_usage(cur, message.id, message.usage)

                cur.execute(
                    """
                    UPDATE app.t_dim_conversation
                    SET updated_at = %s,
                        last_message_at = %s
                    WHERE id = %s
                    """,
                    (now, now, conversation_id),
                )
            self._commit(conn)

        return self.require(conversation_id)

    def update_message(
        self,
        message_id: str,
        *,
        content: str | None = None,
        status: MessageStatus | None = None,
        citations: list[dict[str, Any]] | None = None,
        sources: dict[str, Any] | None = None,
        usage: MessageUsage | None = None,
    ) -> MessageRecord:
        now = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                updates: list[str] = ["updated_at = %s"]
                params: list[Any] = [now]

                if content is not None:
                    updates.append("content = %s")
                    params.append(content)
                if status is not None:
                    updates.append("status = %s")
                    params.append(status.value)
                if citations is not None:
                    updates.append("citations_json = %s")
                    params.append(Json(citations))
                if sources is not None:
                    updates.append("sources_json = %s")
                    params.append(Json(sources))

                params.append(message_id)
                cur.execute(
                    f"""
                    UPDATE app.t_fact_message
                    SET {", ".join(updates)}
                    WHERE id = %s
                    RETURNING conversation_id
                    """,
                    params,
                )
                row = cur.fetchone()
                if row is None:
                    raise MessageNotFoundInRepositoryError(message_id)

                conversation_id = row["conversation_id"]
                if usage is not None:
                    self._upsert_usage(cur, message_id, usage)

                cur.execute(
                    """
                    UPDATE app.t_dim_conversation
                    SET updated_at = %s,
                        last_message_at = %s
                    WHERE id = %s
                    """,
                    (now, now, conversation_id),
                )
            self._commit(conn)

        message = self.find_message_by_id(message_id)
        if message is None:
            raise MessageNotFoundInRepositoryError(message_id)
        return message

    def update(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        folder: str | None = None,
        pinned: bool | None = None,
        clear_folder: bool = False,
    ) -> ConversationRecord:
        now = datetime.now(UTC)
        with self._connect() as conn:
            if self._fetch_conversation_row(conn, conversation_id) is None:
                raise ConversationNotFoundError(conversation_id)

            sets = ["updated_at = %s"]
            params: list[Any] = [now]
            if title is not None:
                sets.append("title = %s")
                params.append(title)
            if clear_folder:
                sets.append("folder_id = NULL")
            elif folder is not None:
                sets.append("folder_id = %s")
                params.append(folder)
            if pinned is not None:
                sets.append("pinned = %s")
                params.append(pinned)

            params.append(conversation_id)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE app.t_dim_conversation
                    SET {", ".join(sets)}
                    WHERE id = %s
                    """,
                    params,
                )
            self._commit(conn)

        return self.require(conversation_id)

    def soft_delete(self, conversation_id: str) -> None:
        now = datetime.now(UTC)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.t_dim_conversation
                    SET status = 'deleted', updated_at = %s
                    WHERE id = %s AND status <> 'deleted'
                    """,
                    (now, conversation_id),
                )
                if cur.rowcount == 0:
                    raise ConversationNotFoundError(conversation_id)
            self._commit(conn)

    def bind_knowledge_bases(
        self,
        conversation_id: str,
        kb_ids: list[str],
    ) -> ConversationRecord:
        unique_ids = list(dict.fromkeys(kb_ids))
        now = datetime.now(UTC)
        with self._connect() as conn:
            if self._fetch_conversation_row(conn, conversation_id) is None:
                raise ConversationNotFoundError(conversation_id)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.t_fact_conversation_kb
                    SET is_active = false, updated_at = %s
                    WHERE conversation_id = %s
                    """,
                    (now, conversation_id),
                )
                for kb_id in unique_ids:
                    link_id = f"convkb_{uuid.uuid4().hex[:12]}"
                    cur.execute(
                        """
                        INSERT INTO app.t_fact_conversation_kb (
                            id, conversation_id, kb_id, is_active, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, true, %s, %s)
                        ON CONFLICT (conversation_id, kb_id)
                        DO UPDATE SET
                            is_active = true,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (link_id, conversation_id, kb_id, now, now),
                    )
                cur.execute(
                    """
                    UPDATE app.t_dim_conversation
                    SET updated_at = %s
                    WHERE id = %s
                    """,
                    (now, conversation_id),
                )
            self._commit(conn)

        return self.require(conversation_id)

    def find_message_by_id(self, message_id: str) -> MessageRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT m.*,
                           u.prompt_tokens,
                           u.completion_tokens,
                           u.total_tokens
                    FROM app.t_fact_message m
                    LEFT JOIN app.t_fact_message_usage u ON u.message_id = m.id
                    WHERE m.id = %s
                    """,
                    (message_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return self._message_from_row(dict(row))

    def upsert_feedback(
        self,
        *,
        message_id: str,
        user_id: str,
        rating: str,
        comment: str | None,
        created_at: str,
    ) -> None:
        feedback_id = f"fb_{uuid.uuid4().hex[:12]}"
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.t_fact_message_feedback (
                        id, message_id, user_id, rating, comment, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (message_id, user_id)
                    DO UPDATE SET
                        rating = EXCLUDED.rating,
                        comment = EXCLUDED.comment,
                        created_at = EXCLUDED.created_at
                    """,
                    (feedback_id, message_id, user_id, rating, comment, created),
                )
            self._commit(conn)

    def _fetch_conversation_row(
        self,
        conn: psycopg.Connection,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM app.t_dim_conversation
                WHERE id = %s AND status <> 'deleted'
                """,
                (conversation_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return dict(row)

    def _fetch_messages(
        self,
        conn: psycopg.Connection,
        conversation_id: str,
    ) -> list[MessageRecord]:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.*,
                       u.prompt_tokens,
                       u.completion_tokens,
                       u.total_tokens
                FROM app.t_fact_message m
                LEFT JOIN app.t_fact_message_usage u ON u.message_id = m.id
                WHERE m.conversation_id = %s
                ORDER BY m.created_at ASC, m.id ASC
                """,
                (conversation_id,),
            )
            rows = cur.fetchall() or []
        return [self._message_from_row(dict(row)) for row in rows]

    def _fetch_kb_ids(self, conn: psycopg.Connection, conversation_id: str) -> list[str]:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT kb_id
                FROM app.t_fact_conversation_kb
                WHERE conversation_id = %s AND is_active = true
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            )
            rows = cur.fetchall() or []
        return [str(row["kb_id"]) for row in rows]

    def _conversation_from_row(
        self,
        row: dict[str, Any],
        *,
        messages: list[MessageRecord],
    ) -> ConversationRecord:
        status = ConversationStatus(str(row["status"]))
        folder_id = row.get("folder_id")
        return ConversationRecord(
            id=str(row["id"]),
            title=str(row["title"]),
            folder=str(folder_id) if folder_id else None,
            pinned=bool(row.get("pinned", False)),
            knowledge_base_ids=[],
            status=status,
            messages=messages,
            created_at=_iso_timestamp(row["created_at"]),
            updated_at=_iso_timestamp(row["updated_at"]),
        )

    def _message_from_row(self, row: dict[str, Any]) -> MessageRecord:
        citations_raw = row.get("citations_json")
        sources_raw = row.get("sources_json")
        citations: list[dict[str, Any]] = []
        sources: dict[str, Any] | None = None

        if citations_raw is not None:
            if isinstance(citations_raw, str):
                citations = json.loads(citations_raw)
            else:
                citations = list(citations_raw)
        if sources_raw is not None:
            if isinstance(sources_raw, str):
                sources = json.loads(sources_raw)
            else:
                sources = dict(sources_raw)

        usage: MessageUsage | None = None
        if row.get("prompt_tokens") is not None:
            usage = MessageUsage(
                prompt_tokens=int(row.get("prompt_tokens") or 0),
                completion_tokens=int(row.get("completion_tokens") or 0),
                total_tokens=int(row.get("total_tokens") or 0),
            )

        edited = row.get("edited_at")
        return MessageRecord(
            id=str(row["id"]),
            role=MessageRole(str(row["role"])),
            content=str(row.get("content") or ""),
            status=MessageStatus(str(row["status"])),
            citations=citations,
            sources=sources,
            usage=usage,
            created_at=_iso_timestamp(row["created_at"]),
            edited_at=_iso_timestamp(edited) if edited is not None else None,
        )

    @staticmethod
    def _upsert_usage(
        cur: psycopg.Cursor,
        message_id: str,
        usage: MessageUsage,
    ) -> None:
        usage_id = f"usage_{uuid.uuid4().hex[:12]}"
        cur.execute(
            """
            INSERT INTO app.t_fact_message_usage (
                id,
                message_id,
                prompt_tokens,
                completion_tokens,
                total_tokens
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (message_id)
            DO UPDATE SET
                prompt_tokens = EXCLUDED.prompt_tokens,
                completion_tokens = EXCLUDED.completion_tokens,
                total_tokens = EXCLUDED.total_tokens
            """,
            (
                usage_id,
                message_id,
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
            ),
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
