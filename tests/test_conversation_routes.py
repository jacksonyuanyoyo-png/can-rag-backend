from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories.conversation_repository import ConversationRepository
from app.services.conversation_service import ConversationService


@pytest.fixture
def client() -> Iterator[TestClient]:
    conversation_service = ConversationService(ConversationRepository())
    with TestClient(app) as test_client:
        test_client.app.state.conversation_service = conversation_service
        yield test_client


def test_conversation_crud_and_messages(client: TestClient) -> None:
    create_response = client.post(
        "/v1/conversations",
        json={"title": "New chat", "folder": "Work Projects", "pinned": False},
    )
    assert create_response.status_code == 201
    created = create_response.json()
    assert "requestId" in created
    conversation = created["data"]
    assert conversation["title"] == "New chat"
    assert conversation["folder"] == "Work Projects"
    assert conversation["pinned"] is False
    assert conversation["messageCount"] == 0
    assert conversation["preview"] == ""
    conversation_id = conversation["id"]

    list_response = client.get("/v1/conversations")
    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["pagination"] == {
        "page": 1,
        "pageSize": 20,
        "total": 1,
        "hasMore": False,
    }
    assert listed["data"][0]["id"] == conversation_id

    detail_response = client.get(f"/v1/conversations/{conversation_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["id"] == conversation_id

    empty_messages = client.get(f"/v1/conversations/{conversation_id}/messages")
    assert empty_messages.status_code == 200
    assert empty_messages.json()["data"] == []

    send_response = client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"content": "Hello backend", "modelId": "gpt-5"},
    )
    assert send_response.status_code == 200
    payload = send_response.json()["data"]
    assert payload["userMessage"]["role"] == "user"
    assert payload["userMessage"]["content"] == "Hello backend"
    assert payload["assistantMessage"]["role"] == "assistant"
    assert payload["assistantMessage"]["status"] == "completed"
    assert payload["assistantMessage"]["citations"] == []
    assert payload["assistantMessage"]["usage"]["totalTokens"] > 0

    messages_response = client.get(f"/v1/conversations/{conversation_id}/messages")
    assert messages_response.status_code == 200
    messages = messages_response.json()["data"]
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[0]["editedAt"] is None

    updated = client.get(f"/v1/conversations/{conversation_id}").json()["data"]
    assert updated["messageCount"] == 2
    assert updated["preview"] == "Mock reply: Hello backend"


def test_get_missing_conversation_returns_not_found(client: TestClient) -> None:
    response = client.get("/v1/conversations/conv_missing")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "CONVERSATION_NOT_FOUND"
    assert body["error"]["details"]["conversationId"] == "conv_missing"
    assert "requestId" in body
