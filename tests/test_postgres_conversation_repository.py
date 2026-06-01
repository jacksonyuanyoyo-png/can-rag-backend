from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.conversation import (
    MessageRecord,
    MessageRole,
    MessageStatus,
    MessageUsage,
    new_message_id,
)
from app.repositories.conversation_repository import ConversationNotFoundError
from app.repositories.postgres_conversation_repository import PostgresConversationRepository


@pytest.fixture
def conversation_repo(database_url: str, db_connection) -> PostgresConversationRepository:
    repo = PostgresConversationRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return repo


def test_create_add_messages_and_reload(conversation_repo: PostgresConversationRepository) -> None:
    conversation = conversation_repo.create(title="PG multi-turn")
    user = MessageRecord(
        id=new_message_id(),
        role=MessageRole.USER,
        content="First question",
        status=MessageStatus.COMPLETED,
    )
    assistant = MessageRecord(
        id=new_message_id(),
        role=MessageRole.ASSISTANT,
        content="First answer",
        status=MessageStatus.COMPLETED,
        citations=[{"index": 1, "snippet": "excerpt"}],
        sources={"segments": []},
        usage=MessageUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    )
    conversation_repo.add_messages(conversation.id, [user, assistant])

    reloaded = conversation_repo.require(conversation.id)
    assert len(reloaded.messages) == 2
    assert reloaded.messages[0].content == "First question"
    assert reloaded.messages[1].content == "First answer"
    assert reloaded.messages[1].citations[0]["index"] == 1
    assert reloaded.messages[1].usage is not None
    assert reloaded.messages[1].usage.total_tokens == 3


def test_update_message_persists_streaming_result(
    conversation_repo: PostgresConversationRepository,
) -> None:
    conversation = conversation_repo.create(title="Stream persist")
    user = MessageRecord(
        id=new_message_id(),
        role=MessageRole.USER,
        content="Hello",
        status=MessageStatus.COMPLETED,
    )
    assistant = MessageRecord(
        id=new_message_id(),
        role=MessageRole.ASSISTANT,
        content="",
        status=MessageStatus.STREAMING,
    )
    conversation_repo.add_messages(conversation.id, [user, assistant])

    conversation_repo.update_message(
        assistant.id,
        content="Final reply",
        status=MessageStatus.COMPLETED,
        citations=[{"index": 1}],
        usage=MessageUsage(prompt_tokens=4, completion_tokens=5, total_tokens=9),
    )

    stored = conversation_repo.find_message_by_id(assistant.id)
    assert stored is not None
    assert stored.content == "Final reply"
    assert stored.status == MessageStatus.COMPLETED
    assert stored.citations == [{"index": 1}]
    assert stored.usage is not None
    assert stored.usage.total_tokens == 9


def test_soft_delete_marks_conversation_missing(conversation_repo: PostgresConversationRepository) -> None:
    conversation = conversation_repo.create(title="To delete")
    conversation_repo.soft_delete(conversation.id)
    assert conversation_repo.get(conversation.id) is None


def test_update_conversation_metadata(conversation_repo: PostgresConversationRepository) -> None:
    conversation = conversation_repo.create(title="Old title", pinned=False)
    updated = conversation_repo.update(conversation.id, title="New title", pinned=True)
    assert updated.title == "New title"
    assert updated.pinned is True


def test_find_message_by_id(conversation_repo: PostgresConversationRepository) -> None:
    conversation = conversation_repo.create(title="Lookup")
    user = MessageRecord(
        id=new_message_id(),
        role=MessageRole.USER,
        content="Ping",
        status=MessageStatus.COMPLETED,
    )
    conversation_repo.add_messages(conversation.id, [user])
    found = conversation_repo.find_message_by_id(user.id)
    assert found is not None
    assert found.content == "Ping"


def test_upsert_feedback(conversation_repo: PostgresConversationRepository) -> None:
    conversation = conversation_repo.create(title="Feedback")
    assistant = MessageRecord(
        id=new_message_id(),
        role=MessageRole.ASSISTANT,
        content="Answer",
        status=MessageStatus.COMPLETED,
    )
    conversation_repo.add_messages(conversation.id, [assistant])
    conversation_repo.upsert_feedback(
        message_id=assistant.id,
        user_id=f"user_{uuid4().hex[:8]}",
        rating="positive",
        comment="helpful",
        created_at="2026-05-30T12:00:00+00:00",
    )


def test_require_missing_raises(conversation_repo: PostgresConversationRepository) -> None:
    with pytest.raises(ConversationNotFoundError):
        conversation_repo.require("conv_missing")
