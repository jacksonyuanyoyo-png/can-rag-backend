from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("RAG_BACKEND", "local")

from app.core.config import get_settings
from app.domain.conversation import ConversationStatus, MessageRecord, MessageRole, MessageStatus, new_message_id
from app.domain.knowledge_base import KnowledgeBaseMetadata, SearchHit
from app.main import app
from app.repositories.conversation_repository import ConversationRepository
from app.services.conversation_service import (
    ActiveGeneration,
    MAX_MESSAGE_LENGTH,
    ConversationService,
)
from tests.sse_utils import parse_sse_events
from tests.test_conversation_stream import StubKnowledgeBaseService


@pytest.fixture
def conversation_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(
        "app.core.bootstrap.initialize_database",
        lambda _settings: {"configured": False, "status": "disabled"},
    )
    monkeypatch.setattr("app.core.bootstrap.is_database_configured", lambda _settings: False)
    from tests.fake_openai_chat import FakeOpenAIChatService

    conversation_service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
        knowledge_base_service=None,
    )
    with TestClient(app) as test_client:
        test_client.app.state.conversation_service = conversation_service
        yield test_client


def _create_conversation(client: TestClient, *, title: str = "API test chat") -> str:
    response = client.post("/v1/conversations", json={"title": title, "pinned": False})
    assert response.status_code == 201
    return response.json()["data"]["id"]


def _send_message(client: TestClient, conversation_id: str, content: str = "Hello") -> dict:
    response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": content, "modelId": "gpt-5"},
    )
    return response


def _archive_conversation(client: TestClient, conversation_id: str) -> None:
    service: ConversationService = client.app.state.conversation_service
    conversation = service.require_conversation(conversation_id)
    conversation.status = ConversationStatus.ARCHIVED


def test_create_conversation_happy_path(conversation_client: TestClient) -> None:
    response = conversation_client.post(
        "/v1/conversations",
        json={"title": "New chat", "folder": "Work Projects", "pinned": True},
    )
    assert response.status_code == 201
    body = response.json()
    assert "requestId" in body
    data = body["data"]
    assert data["title"] == "New chat"
    assert data["folder"] == "Work Projects"
    assert data["pinned"] is True
    assert data["messageCount"] == 0
    assert data["preview"] == ""


def test_list_conversations_pagination_and_search(conversation_client: TestClient) -> None:
    conversation_client.post("/v1/conversations", json={"title": "Alpha project"})
    conversation_client.post("/v1/conversations", json={"title": "Beta notes"})

    page_one = conversation_client.get("/v1/conversations", params={"page": 1, "pageSize": 1})
    assert page_one.status_code == 200
    payload = page_one.json()
    assert payload["pagination"] == {
        "page": 1,
        "pageSize": 1,
        "total": 2,
        "hasMore": True,
    }
    assert len(payload["data"]) == 1

    search = conversation_client.get("/v1/conversations", params={"q": "beta"})
    assert search.status_code == 200
    titles = [item["title"] for item in search.json()["data"]]
    assert titles == ["Beta notes"]


def test_patch_conversation_updates_fields(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client, title="Before rename")

    response = conversation_client.patch(
        f"/v1/conversations/{conversation_id}",
        json={"title": "After rename", "pinned": True, "folder": "Archive"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["title"] == "After rename"
    assert data["pinned"] is True
    assert data["folder"] == "Archive"


def test_delete_conversation_happy_path(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client)

    delete_response = conversation_client.delete(f"/v1/conversations/{conversation_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["data"] == {"success": True}

    list_response = conversation_client.get("/v1/conversations")
    assert all(item["id"] != conversation_id for item in list_response.json()["data"])

    detail_response = conversation_client.get(f"/v1/conversations/{conversation_id}")
    assert detail_response.status_code == 404
    assert detail_response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


def test_send_message_and_list_messages_happy_path(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client)

    send_response = _send_message(conversation_client, conversation_id, "Hello backend")
    assert send_response.status_code == 200
    payload = send_response.json()["data"]
    assert payload["userMessage"]["role"] == "user"
    assert payload["assistantMessage"]["status"] == "completed"
    assert payload["assistantMessage"]["usage"]["totalTokens"] > 0

    messages_response = conversation_client.get(f"/v1/conversations/{conversation_id}/messages")
    assert messages_response.status_code == 200
    messages = messages_response.json()["data"]
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


def test_message_feedback_happy_path(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client)
    send_response = _send_message(conversation_client, conversation_id, "Feedback target")
    assistant_message_id = send_response.json()["data"]["assistantMessage"]["id"]

    feedback_response = conversation_client.post(
        f"/v1/messages/{assistant_message_id}/feedback",
        json={"rating": "positive", "comment": "Helpful answer"},
    )
    assert feedback_response.status_code == 200
    data = feedback_response.json()["data"]
    assert data["messageId"] == assistant_message_id
    assert data["rating"] == "positive"
    assert data["comment"] == "Helpful answer"
    assert "createdAt" in data


def test_sse_stream_happy_path_emits_core_events(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client, title="Stream happy path")

    with conversation_client.stream(
        "POST",
        f"/v1/conversations/{conversation_id}/messages:stream",
        json={"content": "What is the policy?", "modelId": "gpt-5", "knowledgeBaseIds": ["kb_001"]},
        headers={"Accept": "text/event-stream"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    events = parse_sse_events(body)
    event_names = [name for name, _ in events]
    assert event_names[0] == "message.created"
    assert "message.delta" in event_names
    assert event_names[-2:] == ["message.completed", "done"]


def test_cancel_message_endpoint_happy_path(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client, title="Cancel happy path")
    service: ConversationService = conversation_client.app.state.conversation_service
    conversation = service.require_conversation(conversation_id)
    assistant_message_id = new_message_id()
    conversation.messages.extend(
        [
            MessageRecord(
                id=new_message_id(),
                role=MessageRole.USER,
                content="streaming",
                status=MessageStatus.COMPLETED,
            ),
            MessageRecord(
                id=assistant_message_id,
                role=MessageRole.ASSISTANT,
                content="",
                status=MessageStatus.STREAMING,
            ),
        ]
    )
    service._active_generations[conversation_id] = ActiveGeneration(
        assistant_message_id=assistant_message_id,
        cancel_event=asyncio.Event(),
    )

    cancel_response = conversation_client.post(
        f"/v1/conversations/{conversation_id}/messages/{assistant_message_id}:cancel",
    )
    assert cancel_response.status_code == 200
    assert cancel_response.json()["data"] == {
        "messageId": assistant_message_id,
        "status": "cancelled",
    }


def test_get_conversation_not_found(conversation_client: TestClient) -> None:
    response = conversation_client.get("/v1/conversations/conv_missing")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "CONVERSATION_NOT_FOUND"
    assert body["error"]["details"]["conversationId"] == "conv_missing"


def test_list_messages_not_found(conversation_client: TestClient) -> None:
    response = conversation_client.get("/v1/conversations/conv_missing/messages")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


def test_send_message_empty_content_returns_message_empty(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client)
    response = _send_message(conversation_client, conversation_id, "   ")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MESSAGE_EMPTY"


def test_send_message_too_long_returns_message_too_long(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client)
    response = _send_message(conversation_client, conversation_id, "x" * (MAX_MESSAGE_LENGTH + 1))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MESSAGE_TOO_LONG"


def test_send_message_archived_conversation_returns_conflict(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client)
    _archive_conversation(conversation_client, conversation_id)

    response = _send_message(conversation_client, conversation_id, "Should fail")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONVERSATION_ARCHIVED"


def test_stream_validation_errors_emit_message_failed(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client)
    _archive_conversation(conversation_client, conversation_id)

    with conversation_client.stream(
        "POST",
        f"/v1/conversations/{conversation_id}/messages:stream",
        json={"content": "   ", "modelId": "gpt-5"},
        headers={"Accept": "text/event-stream"},
    ) as empty_response:
        empty_body = "".join(empty_response.iter_text())
    empty_events = parse_sse_events(empty_body)
    assert empty_events[0] == (
        "message.failed",
        {
            "code": "MESSAGE_EMPTY",
            "message": "Message content must not be empty",
            "details": {"conversationId": conversation_id},
        },
    )

    with conversation_client.stream(
        "POST",
        f"/v1/conversations/{conversation_id}/messages:stream",
        json={"content": "hello", "modelId": "gpt-5"},
        headers={"Accept": "text/event-stream"},
    ) as archived_response:
        archived_body = "".join(archived_response.iter_text())
    archived_events = parse_sse_events(archived_body)
    assert archived_events[0][1]["code"] == "CONVERSATION_ARCHIVED"


def test_send_message_while_stream_running_returns_message_already_running(
    conversation_client: TestClient,
) -> None:
    conversation_id = _create_conversation(conversation_client)
    service: ConversationService = conversation_client.app.state.conversation_service
    service._active_generations[conversation_id] = ActiveGeneration(
        assistant_message_id="msg_streaming",
        cancel_event=asyncio.Event(),
    )

    conflict = _send_message(conversation_client, conversation_id, "Second message")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "MESSAGE_ALREADY_RUNNING"


def test_stream_missing_conversation_emits_not_found(conversation_client: TestClient) -> None:
    with conversation_client.stream(
        "POST",
        "/v1/conversations/conv_missing/messages:stream",
        json={"content": "hello"},
        headers={"Accept": "text/event-stream"},
    ) as response:
        body = "".join(response.iter_text())

    events = parse_sse_events(body)
    assert events[0][0] == "message.failed"
    assert events[0][1]["code"] == "CONVERSATION_NOT_FOUND"
    assert events[-1] == ("done", {})


def test_cancel_unknown_message_returns_resource_not_found(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client)
    response = conversation_client.post(
        f"/v1/conversations/{conversation_id}/messages/msg_missing:cancel",
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert response.json()["error"]["details"]["messageId"] == "msg_missing"


def test_cancel_completed_message_returns_message_already_running(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client)
    send_response = _send_message(conversation_client, conversation_id, "Completed assistant")
    assistant_message_id = send_response.json()["data"]["assistantMessage"]["id"]

    response = conversation_client.post(
        f"/v1/conversations/{conversation_id}/messages/{assistant_message_id}:cancel",
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MESSAGE_ALREADY_RUNNING"
    assert response.json()["error"]["details"]["status"] == "completed"


def test_message_feedback_invalid_rating_returns_validation_error(conversation_client: TestClient) -> None:
    conversation_id = _create_conversation(conversation_client)
    send_response = _send_message(conversation_client, conversation_id, "Rate me")
    assistant_message_id = send_response.json()["data"]["assistantMessage"]["id"]

    response = conversation_client.post(
        f"/v1/messages/{assistant_message_id}/feedback",
        json={"rating": "thumbs_up"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_send_kb_binding_persists_and_falls_back_on_second_request() -> None:
    from tests.fake_openai_chat import FakeOpenAIChatService

    kb_uuid = "kb_send_bind_001"
    metadata = KnowledgeBaseMetadata(name="send-bound", id=kb_uuid)
    hit = SearchHit(
        document_id="doc_send",
        file_name="send.pdf",
        chunk_id="chunk_send",
        text="Send bound excerpt",
        score=0.85,
        citation={},
    )
    kb_service = StubKnowledgeBaseService(kbs={kb_uuid: metadata}, hits=[hit])
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
        knowledge_base_service=kb_service,
    )
    with TestClient(app) as client:
        client.app.state.conversation_service = service
        create_response = client.post("/v1/conversations", json={"title": "Send KB bind"})
        conversation_id = create_response.json()["data"]["id"]

        first = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={"content": "First with KB", "modelId": "gpt-5", "knowledgeBaseIds": [kb_uuid]},
        )
        assert first.status_code == 200
        assert kb_service.search_calls
        assert kb_service.search_calls[0]["kb_id"] == kb_uuid
        kb_service.search_calls.clear()

        second = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={"content": "Second without KB", "modelId": "gpt-5"},
        )
        assert second.status_code == 200
        assert kb_service.search_calls
        assert kb_service.search_calls[0]["kb_id"] == kb_uuid
        assert second.json()["data"]["assistantMessage"]["citations"]

    stored = service.require_conversation(conversation_id)
    assert stored.knowledge_base_ids == [kb_uuid]


def test_send_without_bound_kb_has_empty_citations() -> None:
    from tests.fake_openai_chat import FakeOpenAIChatService

    kb_service = StubKnowledgeBaseService()
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
        knowledge_base_service=kb_service,
    )
    with TestClient(app) as client:
        client.app.state.conversation_service = service
        conversation_id = _create_conversation(client, title="Send no KB")
        response = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={"content": "No KB question", "modelId": "gpt-5"},
        )
        assert response.status_code == 200
        assert kb_service.search_calls == []
        assert response.json()["data"]["assistantMessage"]["citations"] == []


def test_message_feedback_unknown_message_returns_resource_not_found(conversation_client: TestClient) -> None:
    response = conversation_client.post(
        "/v1/messages/msg_missing/feedback",
        json={"rating": "negative"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert response.json()["error"]["details"]["messageId"] == "msg_missing"
