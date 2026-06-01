from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.conversation import MessageUsage
from app.domain.knowledge_base import KnowledgeBaseMetadata, SearchHit
from app.main import app
from app.repositories.conversation_repository import ConversationRepository
from app.services.conversation_service import ConversationService
from tests.fake_openai_chat import FakeOpenAIChatService


class StubKnowledgeBaseService:
    def __init__(
        self,
        *,
        kbs: dict[str, KnowledgeBaseMetadata] | None = None,
        hits: list[SearchHit] | None = None,
    ) -> None:
        self._kbs = kbs or {}
        self._hits = hits or []
        self.search_calls: list[dict[str, Any]] = []

    def find_kb_by_id(self, kb_id: str) -> KnowledgeBaseMetadata | None:
        return self._kbs.get(kb_id)

    def search(
        self,
        *,
        knowledge_base: str,
        query: str,
        top_k: int = 5,
        kb_id: str | None = None,
    ) -> list[SearchHit]:
        self.search_calls.append(
            {
                "knowledge_base": knowledge_base,
                "query": query,
                "top_k": top_k,
                "kb_id": kb_id,
            }
        )
        return list(self._hits)


SSE_EVENT_PATTERN = re.compile(r"^event: (.+)\ndata: (.+)$", re.MULTILINE)


def parse_sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for match in SSE_EVENT_PATTERN.finditer(body.strip()):
        event_name = match.group(1)
        data = json.loads(match.group(2))
        events.append((event_name, data))
    return events


def test_stream_message_emits_stable_event_sequence() -> None:
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
    )
    with TestClient(app) as client:
        client.app.state.conversation_service = service
        conversation = service.create_conversation(title="Stream test")

        with client.stream(
            "POST",
            f"/v1/conversations/{conversation.id}/messages:stream",
            json={"content": "What is the policy?", "modelId": "gpt-5", "knowledgeBaseIds": ["kb_001"]},
            headers={"Accept": "text/event-stream"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(response.iter_text())

    events = parse_sse_events(body)
    event_names = [name for name, _ in events]
    assert event_names[0] == "message.created"
    assert event_names[1:3] == ["retrieval.started", "retrieval.completed"]
    assert "message.delta" in event_names
    assert "usage.completed" in event_names
    assert event_names[-2:] == ["message.completed", "done"]

    created = events[0][1]
    assistant_message_id = created["assistantMessageId"]
    delta_events = [data for name, data in events if name == "message.delta"]
    completed = events[-2][1]

    assert created["conversationId"] == conversation.id
    assert delta_events
    assert all(item["messageId"] == assistant_message_id for item in delta_events)
    assert completed["messageId"] == assistant_message_id
    assert completed["status"] == "completed"
    assert completed["content"] == "".join(item["delta"] for item in delta_events)

    stored_messages = service.list_messages(conversation.id)
    assistant_message = stored_messages[-1]
    assert assistant_message.content == completed["content"]
    assert assistant_message.status.value == "completed"


def test_cancel_message_marks_generation_cancelled() -> None:
    async def run() -> list[tuple[str, dict[str, Any]]]:
        service = ConversationService(
            ConversationRepository(),
            settings=get_settings(),
            chat_service=FakeOpenAIChatService(),
        )
        conversation = service.create_conversation(title="Cancel test")
        collected: list[tuple[str, dict[str, Any]]] = []
        cancelled = False

        assistant_message_id = ""
        async for event in service.stream_message(
            conversation_id=conversation.id,
            content="Long enough content for cancellation test",
            model_id="gpt-5",
        ):
            collected.append((event.event, event.data))
            if event.event == "message.created":
                assistant_message_id = event.data["assistantMessageId"]
            if event.event == "message.delta" and not cancelled:
                cancelled = True
                await service.cancel_message(
                    conversation_id=conversation.id,
                    message_id=assistant_message_id,
                )

        return collected

    events = asyncio.run(run())
    event_names = [name for name, _ in events]
    completed = next(data for name, data in events if name == "message.completed")

    assert "message.delta" in event_names
    assert event_names[-1] == "done"
    assert completed["status"] == "cancelled"


def test_cancel_endpoint_returns_cancelled_status() -> None:
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
    )
    with TestClient(app) as client:
        client.app.state.conversation_service = service
        conversation = service.create_conversation(title="Cancel API test")

        async def run_stream_and_cancel() -> str:
            svc: ConversationService = client.app.state.conversation_service
            assistant_message_id = ""

            async for event in svc.stream_message(
                conversation_id=conversation.id,
                content="trigger streaming",
                model_id="gpt-5",
            ):
                if event.event == "message.created":
                    assistant_message_id = event.data["assistantMessageId"]
                    await svc.cancel_message(
                        conversation_id=conversation.id,
                        message_id=assistant_message_id,
                    )

            return assistant_message_id

        assistant_message_id = asyncio.run(run_stream_and_cancel())
        assert assistant_message_id

        cancel_response = client.post(
            f"/v1/conversations/{conversation.id}/messages/{assistant_message_id}:cancel",
        )
        assert cancel_response.status_code == 200
        assert cancel_response.json()["data"] == {
            "messageId": assistant_message_id,
            "status": "cancelled",
        }


def test_stream_retrieval_uses_kb_id_and_name_for_bound_kb() -> None:
    kb_uuid = "kb_a1b2c3d4e5f6"
    kb_name = "policy-docs"
    metadata = KnowledgeBaseMetadata(name=kb_name, id=kb_uuid)
    hit = SearchHit(
        document_id="doc_001",
        file_name="policy.pdf",
        chunk_id="chunk_001",
        text="Retirement policy excerpt",
        score=0.91,
        citation={"page": 1},
    )
    kb_service = StubKnowledgeBaseService(kbs={kb_uuid: metadata}, hits=[hit])
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
        knowledge_base_service=kb_service,
    )
    conversation = service.create_conversation(title="KB retrieval test")
    conversation.knowledge_base_ids = [kb_uuid]

    with TestClient(app) as client:
        client.app.state.conversation_service = service
        with client.stream(
            "POST",
            f"/v1/conversations/{conversation.id}/messages:stream",
            json={"content": "What is the retirement policy?", "modelId": "gpt-5"},
            headers={"Accept": "text/event-stream"},
        ) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())

    events = parse_sse_events(body)
    retrieval_completed = next(data for name, data in events if name == "retrieval.completed")
    citations = retrieval_completed["citations"]
    assert len(citations) == 1
    assert citations[0]["fileId"] == "doc_001"
    assert citations[0]["snippet"] == "Retirement policy excerpt"
    assert kb_service.search_calls
    assert kb_service.search_calls[0]["knowledge_base"] == kb_name
    assert kb_service.search_calls[0]["kb_id"] == kb_uuid


def test_stream_retrieval_missing_kb_yields_empty_citations_and_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    kb_service = StubKnowledgeBaseService()
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
        knowledge_base_service=kb_service,
    )
    conversation = service.create_conversation(title="Missing KB test")
    missing_kb_id = "kb_does_not_exist"

    with caplog.at_level(logging.WARNING):
        with TestClient(app) as client:
            client.app.state.conversation_service = service
            with client.stream(
                "POST",
                f"/v1/conversations/{conversation.id}/messages:stream",
                json={
                    "content": "Any question?",
                    "modelId": "gpt-5",
                    "knowledgeBaseIds": [missing_kb_id],
                },
                headers={"Accept": "text/event-stream"},
            ) as response:
                assert response.status_code == 200
                body = "".join(response.iter_text())

    events = parse_sse_events(body)
    retrieval_completed = next(data for name, data in events if name == "retrieval.completed")
    assert retrieval_completed["citations"] == []
    assert kb_service.search_calls == []
    assert any(
        "Knowledge base not found for retrieval" in record.message and missing_kb_id in record.message
        for record in caplog.records
    )


async def _collect_stream_events(
    service: ConversationService,
    conversation_id: str,
    *,
    content: str,
    knowledge_base_ids: list[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    collected: list[tuple[str, dict[str, Any]]] = []
    async for event in service.stream_message(
        conversation_id=conversation_id,
        content=content,
        model_id="gpt-5",
        knowledge_base_ids=knowledge_base_ids,
    ):
        collected.append((event.event, event.data))
    return collected


def test_stream_kb_binding_persists_and_falls_back_on_second_request() -> None:
    kb_uuid = "kb_persist_a1b2c3"
    kb_name = "bound-docs"
    metadata = KnowledgeBaseMetadata(name=kb_name, id=kb_uuid)
    hit = SearchHit(
        document_id="doc_bind",
        file_name="bind.pdf",
        chunk_id="chunk_bind",
        text="Bound excerpt",
        score=0.88,
        citation={},
    )
    kb_service = StubKnowledgeBaseService(kbs={kb_uuid: metadata}, hits=[hit])
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
        knowledge_base_service=kb_service,
    )
    conversation = service.create_conversation(title="KB bind persist")

    asyncio.run(
        _collect_stream_events(
            service,
            conversation.id,
            content="First question with binding",
            knowledge_base_ids=[kb_uuid],
        )
    )
    assert kb_service.search_calls
    assert kb_service.search_calls[0]["kb_id"] == kb_uuid
    kb_service.search_calls.clear()

    asyncio.run(
        _collect_stream_events(
            service,
            conversation.id,
            content="Second question without kb ids",
            knowledge_base_ids=None,
        )
    )
    assert kb_service.search_calls
    assert kb_service.search_calls[0]["kb_id"] == kb_uuid
    stored = service.require_conversation(conversation.id)
    assert stored.knowledge_base_ids == [kb_uuid]


def test_stream_without_bound_kb_yields_empty_citations() -> None:
    kb_service = StubKnowledgeBaseService()
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
        knowledge_base_service=kb_service,
    )
    conversation = service.create_conversation(title="No KB bind")

    events = asyncio.run(
        _collect_stream_events(
            service,
            conversation.id,
            content="Unbound question",
            knowledge_base_ids=None,
        )
    )
    event_names = [name for name, _ in events]
    assert "retrieval.started" not in event_names
    assert kb_service.search_calls == []
    retrieval_completed = next(
        (data for name, data in events if name == "retrieval.completed"),
        None,
    )
    assert retrieval_completed is None
    completed = next(data for name, data in events if name == "message.completed")
    stored = service.list_messages(conversation.id)[-1]
    assert stored.citations == []
    assert completed["status"] == "completed"


def test_stream_returns_failed_event_for_missing_conversation() -> None:
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
    )
    with TestClient(app) as client:
        client.app.state.conversation_service = service
        with client.stream(
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


class CapturingFakeOpenAIChatService(FakeOpenAIChatService):
    def __init__(self) -> None:
        self.last_messages: list[dict[str, str]] = []

    async def stream(self, *, messages, model, cancel_event=None):
        self.last_messages = list(messages)
        async for item in super().stream(messages=messages, model=model, cancel_event=cancel_event):
            yield item

    async def complete(self, *, messages, model):
        self.last_messages = list(messages)
        return await super().complete(messages=messages, model=model)


def test_citations_payload_includes_index_and_deep_link_fields() -> None:
    kb_uuid = "kb_citation_fields"
    metadata = KnowledgeBaseMetadata(name="docs", id=kb_uuid)
    hit = SearchHit(
        document_id="file_abc",
        file_name="policy.pdf",
        chunk_id="data_001",
        text="Policy excerpt text",
        score=0.92,
        citation={"page": 3, "type": "text"},
    )
    kb_service = StubKnowledgeBaseService(kbs={kb_uuid: metadata}, hits=[hit])
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
        knowledge_base_service=kb_service,
    )
    conversation = service.create_conversation(title="Citation fields")
    conversation.knowledge_base_ids = [kb_uuid]

    with TestClient(app) as client:
        client.app.state.conversation_service = service
        with client.stream(
            "POST",
            f"/v1/conversations/{conversation.id}/messages:stream",
            json={"content": "Explain policy", "modelId": "gpt-5"},
            headers={"Accept": "text/event-stream"},
        ) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())

    citations = next(data for name, data in parse_sse_events(body) if name == "retrieval.completed")["citations"]
    assert len(citations) == 1
    item = citations[0]
    assert item["index"] == 1
    assert item["kbId"] == kb_uuid
    assert item["fileId"] == "file_abc"
    assert item["chunkId"] == "data_001"
    assert item["page"] == 3
    assert item["fileName"] == "policy.pdf"
    assert item["snippet"] == "Policy excerpt text"
    assert item["type"] == "text"


def test_build_chat_messages_uses_numbered_sources_and_inline_citation_instruction() -> None:
    chat = CapturingFakeOpenAIChatService()
    kb_uuid = "kb_numbered_ctx"
    metadata = KnowledgeBaseMetadata(name="numbered", id=kb_uuid)
    hits = [
        SearchHit(
            document_id="f1",
            file_name="a.pdf",
            chunk_id="c1",
            text="First snippet",
            score=0.9,
            citation={"page": 1},
        ),
        SearchHit(
            document_id="f2",
            file_name="b.pdf",
            chunk_id="c2",
            text="Second snippet",
            score=0.8,
            citation={},
        ),
    ]
    kb_service = StubKnowledgeBaseService(kbs={kb_uuid: metadata}, hits=hits)
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=chat,
        knowledge_base_service=kb_service,
    )
    conversation = service.create_conversation(title="Numbered context")
    asyncio.run(
        _collect_stream_events(
            service,
            conversation.id,
            content="Question with two sources",
            knowledge_base_ids=[kb_uuid],
        )
    )
    system_messages = [message for message in chat.last_messages if message["role"] == "system"]
    assert system_messages
    system_content = system_messages[0]["content"]
    assert "来源[1]" in system_content
    assert "来源[2]" in system_content
    assert "a.pdf 第1页" in system_content
    assert "First snippet" in system_content
    assert "Second snippet" in system_content
    assert "[1]" in system_content


def test_stream_citations_persist_on_message_reread() -> None:
    kb_uuid = "kb_persist_cite"
    metadata = KnowledgeBaseMetadata(name="persist", id=kb_uuid)
    hit = SearchHit(
        document_id="doc_persist",
        file_name="persist.pdf",
        chunk_id="chunk_persist",
        text="Persisted snippet",
        score=0.87,
        citation={"page": 5},
    )
    kb_service = StubKnowledgeBaseService(kbs={kb_uuid: metadata}, hits=[hit])
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
        knowledge_base_service=kb_service,
    )
    conversation = service.create_conversation(title="Persist citations")
    asyncio.run(
        _collect_stream_events(
            service,
            conversation.id,
            content="Persist test",
            knowledge_base_ids=[kb_uuid],
        )
    )
    assistant = service.list_messages(conversation.id)[-1]
    assert len(assistant.citations) == 1
    assert assistant.citations[0]["index"] == 1
    assert assistant.citations[0]["kbId"] == kb_uuid
    assert assistant.citations[0]["chunkId"] == "chunk_persist"


def test_image_citation_includes_storage_key() -> None:
    kb_uuid = "kb_image_cite"
    metadata = KnowledgeBaseMetadata(name="images", id=kb_uuid)
    hit = SearchHit(
        document_id="file_img",
        file_name="flow.pdf",
        chunk_id="img0001-001",
        text="Flowchart description from VLM",
        score=0.75,
        citation={"type": "image", "storage_key": "kb_images/abc.png", "page": 2},
    )
    kb_service = StubKnowledgeBaseService(kbs={kb_uuid: metadata}, hits=[hit])
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=FakeOpenAIChatService(),
        knowledge_base_service=kb_service,
    )
    citations, sources = service._prepare_retrieval_payload([hit])
    assert citations[0]["type"] == "image"
    assert citations[0]["storageKey"] == "kb_images/abc.png"
    assert citations[0]["page"] == 2
    assert citations[0]["imageAssets"][0]["assetUrl"].startswith("/v1/uploads/assets/")
    assert sources is not None
    assert len(sources["segments"]) == 1
    assert len(sources["figures"]) == 1


def test_build_chat_messages_includes_markdown_image_url_instructions() -> None:
    chat = CapturingFakeOpenAIChatService()
    kb_uuid = "kb_md_images"
    metadata = KnowledgeBaseMetadata(name="md", id=kb_uuid)
    hits = [
        SearchHit(
            document_id="f1",
            file_name="guide.docx",
            chunk_id="c1",
            text="步骤说明\n\n![图示](kb_images/abc.png)",
            score=0.9,
            citation={"kb_id": kb_uuid},
        ),
    ]
    kb_service = StubKnowledgeBaseService(kbs={kb_uuid: metadata}, hits=hits)
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=chat,
        knowledge_base_service=kb_service,
    )
    conversation = service.create_conversation(title="Markdown images")
    asyncio.run(
        _collect_stream_events(
            service,
            conversation.id,
            content="Show the UI steps",
            knowledge_base_ids=[kb_uuid],
        )
    )
    system_content = next(
        message["content"] for message in chat.last_messages if message["role"] == "system"
    )
    assert "Format your entire answer in Markdown" in system_content
    assert "assetUrl" in system_content
    assert "/v1/uploads/assets/kb_images/abc.png" in system_content
    assert "Source [1]:" in system_content


class MarkdownImageReplyFake(FakeOpenAIChatService):
    async def stream(self, *, messages, model, cancel_event=None):
        text = "见下图 ![UI](kb_images/abc.png) [1]"
        yield text, None
        yield "", MessageUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3)


def test_stream_completed_content_rewrites_assistant_markdown_images() -> None:
    kb_uuid = "kb_rewrite_reply"
    metadata = KnowledgeBaseMetadata(name="rewrite", id=kb_uuid)
    hit = SearchHit(
        document_id="f1",
        file_name="guide.docx",
        chunk_id="c1",
        text="![图示](kb_images/abc.png)",
        score=0.9,
        citation={"kb_id": kb_uuid},
    )
    kb_service = StubKnowledgeBaseService(kbs={kb_uuid: metadata}, hits=[hit])
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=MarkdownImageReplyFake(),
        knowledge_base_service=kb_service,
    )
    conversation = service.create_conversation(title="Rewrite reply")
    events = asyncio.run(
        _collect_stream_events(
            service,
            conversation.id,
            content="Show image",
            knowledge_base_ids=[kb_uuid],
        )
    )
    completed = next(data for name, data in events if name == "message.completed")
    assert "![UI](/v1/uploads/assets/kb_images/abc.png)" in completed["content"]
    assert "](kb_images/" not in completed["content"]
    stored = service.list_messages(conversation.id)[-1]
    assert stored.content == completed["content"]


def test_send_message_rewrites_assistant_markdown_images() -> None:
    kb_uuid = "kb_rewrite_sync"
    metadata = KnowledgeBaseMetadata(name="rewrite-sync", id=kb_uuid)
    hit = SearchHit(
        document_id="f1",
        file_name="guide.docx",
        chunk_id="c1",
        text="body",
        score=0.9,
        citation={"kb_id": kb_uuid},
    )

    class SyncImageReplyFake(FakeOpenAIChatService):
        async def complete(self, *, messages, model):
            return "图 ![x](kb_images/sync.jpeg) [1]", MessageUsage()

    kb_service = StubKnowledgeBaseService(kbs={kb_uuid: metadata}, hits=[hit])
    service = ConversationService(
        ConversationRepository(),
        settings=get_settings(),
        chat_service=SyncImageReplyFake(),
        knowledge_base_service=kb_service,
    )
    conversation = service.create_conversation(title="Sync rewrite")
    result = asyncio.run(
        service.send_message(
            conversation_id=conversation.id,
            content="Show",
            knowledge_base_ids=[kb_uuid],
        )
    )
    assert "![x](/v1/uploads/assets/kb_images/sync.jpeg)" in result.assistant_message.content
    assert "](kb_images/" not in result.assistant_message.content
