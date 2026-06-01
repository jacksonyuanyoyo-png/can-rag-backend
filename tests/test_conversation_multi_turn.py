from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.domain.conversation import MessageRole, MessageStatus
from app.repositories.conversation_repository import ConversationRepository
from app.services.conversation_service import ConversationService
from tests.test_conversation_stream import CapturingFakeOpenAIChatService


async def _collect_stream_events(
    service: ConversationService,
    conversation_id: str,
    *,
    content: str,
    model_id: str = "gpt-5",
) -> list:
    events = []
    async for event in service.stream_message(
        conversation_id=conversation_id,
        content=content,
        model_id=model_id,
    ):
        events.append(event)
    return events


def test_stream_second_turn_includes_first_turn_in_llm_messages() -> None:
    chat = CapturingFakeOpenAIChatService()
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=chat,
    )
    conversation = service.create_conversation(title="Multi-turn")

    asyncio.run(
        _collect_stream_events(
            service,
            conversation.id,
            content="What is the retirement age?",
        )
    )
    asyncio.run(
        _collect_stream_events(
            service,
            conversation.id,
            content="Can you elaborate on the second point?",
        )
    )

    roles_and_prefixes = [
        (message["role"], message.get("content", "")[:40])
        for message in chat.last_messages
        if message["role"] in {"user", "assistant"}
    ]
    user_contents = [
        message["content"]
        for message in chat.last_messages
        if message["role"] == "user" and isinstance(message.get("content"), str)
    ]
    assistant_contents = [
        message["content"]
        for message in chat.last_messages
        if message["role"] == "assistant" and isinstance(message.get("content"), str)
    ]

    assert any("retirement age" in text for text in user_contents)
    assert any("elaborate" in text for text in user_contents)
    assert any("Mock reply" in text for text in assistant_contents)
    assert len(roles_and_prefixes) >= 3


def test_build_retrieval_query_includes_prior_user_messages() -> None:
    service = ConversationService(ConversationRepository(), settings=get_settings())
    conversation = service.create_conversation(title="Retrieval query")
    from app.domain.conversation import MessageRecord, new_message_id

    service._repository.add_messages(
        conversation.id,
        [
            MessageRecord(
                id=new_message_id(),
                role=MessageRole.USER,
                content="First topic",
                status=MessageStatus.COMPLETED,
            ),
            MessageRecord(
                id=new_message_id(),
                role=MessageRole.ASSISTANT,
                content="First answer",
                status=MessageStatus.COMPLETED,
            ),
        ],
    )
    conversation = service._repository.require(conversation.id)
    query = service._build_retrieval_query(conversation, "Follow-up on second point")
    assert "First topic" in query
    assert "Follow-up on second point" in query


def test_history_window_limits_llm_messages() -> None:
    settings = get_settings().model_copy(update={"CHAT_HISTORY_MAX_TURNS": 1})
    service = ConversationService(ConversationRepository(), settings=settings)
    conversation = service.create_conversation(title="Window")
    from app.domain.conversation import MessageRecord, new_message_id

    service._repository.add_messages(
        conversation.id,
        [
            MessageRecord(
                id=new_message_id(),
                role=MessageRole.USER,
                content="Old user",
                status=MessageStatus.COMPLETED,
            ),
            MessageRecord(
                id=new_message_id(),
                role=MessageRole.ASSISTANT,
                content="Old assistant",
                status=MessageStatus.COMPLETED,
            ),
            MessageRecord(
                id=new_message_id(),
                role=MessageRole.USER,
                content="New user",
                status=MessageStatus.COMPLETED,
            ),
            MessageRecord(
                id=new_message_id(),
                role=MessageRole.ASSISTANT,
                content="New assistant",
                status=MessageStatus.COMPLETED,
            ),
        ],
    )
    conversation = service._repository.require(conversation.id)
    history = service._select_history_messages(conversation)
    assert len(history) == 2
    assert history[0].content == "New user"
    assert history[1].content == "New assistant"
