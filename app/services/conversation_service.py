from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import BusinessError, ErrorCode
from app.domain.conversation import (
    ConversationRecord,
    ConversationStatus,
    MessageRecord,
    MessageRole,
    MessageStatus,
    MessageUsage,
    new_message_id,
)
from app.domain.knowledge_base import SearchHit, utc_now_iso
from app.repositories.conversation_repository import ConversationNotFoundError
from app.services.knowledge_base_adapter import KnowledgeBaseNotFoundError, require_kb
from app.services.chat_vision import append_citation_figures_to_messages
from app.services.citation_sources import (
    build_message_sources,
    citations_from_hits,
)
from app.services.markdown_render import rewrite_markdown_asset_urls
from app.services.openai_chat_service import OpenAIChatError, OpenAIChatService

if TYPE_CHECKING:
    from app.services.knowledge_base_service import KnowledgeBaseService


class ConversationRepositoryProtocol(Protocol):
    def create(self, *, title: str, folder: str | None = None, pinned: bool = False) -> ConversationRecord: ...

    def get(self, conversation_id: str) -> ConversationRecord | None: ...

    def require(self, conversation_id: str) -> ConversationRecord: ...

    def list_all(self) -> list[ConversationRecord]: ...

    def add_messages(
        self, conversation_id: str, messages: list[MessageRecord]
    ) -> ConversationRecord: ...

    def update(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        folder: str | None = None,
        pinned: bool | None = None,
        clear_folder: bool = False,
    ) -> ConversationRecord: ...

    def soft_delete(self, conversation_id: str) -> None: ...

    def bind_knowledge_bases(self, conversation_id: str, kb_ids: list[str]) -> ConversationRecord: ...

    def find_message_by_id(self, message_id: str) -> MessageRecord | None: ...

    def update_message(
        self,
        message_id: str,
        *,
        content: str | None = None,
        status: MessageStatus | None = None,
        citations: list[dict[str, Any]] | None = None,
        sources: dict[str, Any] | None = None,
        usage: MessageUsage | None = None,
    ) -> MessageRecord: ...


logger = logging.getLogger(__name__)


MAX_MESSAGE_LENGTH = 32_000
VALID_FEEDBACK_RATINGS = frozenset({"positive", "negative"})
RAG_TOP_K = 5


def _search_kb_citations(
    kb_service: KnowledgeBaseService,
    kb_identifier: str,
    query: str,
) -> list[SearchHit]:
    try:
        metadata = require_kb(kb_service, kb_identifier)
    except KnowledgeBaseNotFoundError:
        logger.warning(
            "Knowledge base not found for retrieval: kb=%s",
            kb_identifier,
        )
        return []
    except Exception as exc:
        logger.warning(
            "Failed to resolve knowledge base for retrieval: kb=%s reason=%s",
            kb_identifier,
            exc,
            exc_info=True,
        )
        return []
    try:
        hits = kb_service.search(
            knowledge_base=metadata.name,
            kb_id=metadata.id,
            query=query,
            top_k=RAG_TOP_K,
        )
        for hit in hits:
            hit.citation["kb_id"] = metadata.id
        return hits
    except Exception as exc:
        logger.warning(
            "Knowledge base search failed: kb=%s name=%s reason=%s",
            kb_identifier,
            metadata.name,
            exc,
            exc_info=True,
        )
        return []


class MessageAlreadyRunningError(Exception):
    pass


class MessageNotFoundError(Exception):
    def __init__(self, message_id: str) -> None:
        self.message_id = message_id
        super().__init__(f"Message not found: {message_id}")


class MessageNotCancellableError(Exception):
    def __init__(self, message_id: str, status: MessageStatus) -> None:
        self.message_id = message_id
        self.status = status
        super().__init__(f"Message {message_id} is not cancellable ({status.value})")


@dataclass(slots=True)
class ActiveGeneration:
    assistant_message_id: str
    cancel_event: asyncio.Event


@dataclass(frozen=True, slots=True)
class SseEvent:
    event: str
    data: dict[str, Any]


@dataclass(slots=True)
class PaginatedConversations:
    items: list[dict[str, Any]]
    page: int
    page_size: int
    total: int

    @property
    def has_more(self) -> bool:
        return self.page * self.page_size < self.total


@dataclass(slots=True)
class SendMessageResult:
    user_message: MessageRecord
    assistant_message: MessageRecord


@dataclass(slots=True)
class MessageFeedbackResult:
    message_id: str
    rating: str
    created_at: str
    comment: str | None = None

    def to_api(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messageId": self.message_id,
            "rating": self.rating,
            "createdAt": self.created_at,
        }
        if self.comment is not None:
            payload["comment"] = self.comment
        return payload


class ConversationService:
    """会话用例服务：REST 与非流式/流式消息；仓储可为内存或 PostgreSQL。"""

    def __init__(
        self,
        repository: ConversationRepositoryProtocol,
        *,
        settings: Settings | None = None,
        chat_service: OpenAIChatService | None = None,
        knowledge_base_service: KnowledgeBaseService | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings or get_settings()
        self._chat = chat_service or OpenAIChatService(self._settings)
        self._knowledge_base_service = knowledge_base_service
        self._lock = asyncio.Lock()
        self._active_generations: dict[str, ActiveGeneration] = {}
        self._message_feedback: dict[tuple[str, str], MessageFeedbackResult] = {}

    def create_conversation(
        self,
        *,
        title: str = "New chat",
        folder: str | None = None,
        pinned: bool = False,
    ) -> ConversationRecord:
        return self._repository.create(title=title, folder=folder, pinned=pinned)

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        return self._repository.get(conversation_id)

    def require_conversation(self, conversation_id: str) -> ConversationRecord:
        return self._repository.require(conversation_id)

    def list_conversations(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        q: str | None = None,
        folder_id: str | None = None,
    ) -> PaginatedConversations:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        items = self._repository.list_all()
        if folder_id is not None:
            items = [item for item in items if item.folder == folder_id]
        if q:
            query = q.casefold()
            items = [
                item
                for item in items
                if query in item.title.casefold() or query in item.to_list_api()["preview"].casefold()
            ]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return PaginatedConversations(
            items=[item.to_list_api() for item in items[start:end]],
            page=page,
            page_size=page_size,
            total=total,
        )

    def list_messages(self, conversation_id: str) -> list[MessageRecord]:
        return list(self._repository.require(conversation_id).messages)

    def update_conversation(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        folder: str | None = None,
        pinned: bool | None = None,
        clear_folder: bool = False,
    ) -> ConversationRecord:
        self._ensure_conversation_mutable(conversation_id)
        if title is None and folder is None and pinned is None and not clear_folder:
            return self._repository.require(conversation_id)
        return self._repository.update(
            conversation_id,
            title=title,
            folder=folder,
            pinned=pinned,
            clear_folder=clear_folder,
        )

    def delete_conversation(self, conversation_id: str) -> dict[str, bool]:
        self._ensure_conversation_mutable(conversation_id)
        self._repository.soft_delete(conversation_id)
        self._active_generations.pop(conversation_id, None)
        return {"success": True}

    def message_feedback(
        self,
        message_id: str,
        *,
        rating: str,
        comment: str | None = None,
        user_id: str = "user_default",
    ) -> MessageFeedbackResult:
        normalized_rating = rating.strip().casefold()
        if normalized_rating not in VALID_FEEDBACK_RATINGS:
            raise BusinessError(
                ErrorCode.VALIDATION_ERROR,
                details={"field": "rating", "allowed": sorted(VALID_FEEDBACK_RATINGS)},
            )

        message = self._repository.find_message_by_id(message_id)
        if message is None:
            raise MessageNotFoundError(message_id)

        created_at = utc_now_iso()
        result = MessageFeedbackResult(
            message_id=message_id,
            rating=normalized_rating,
            created_at=created_at,
            comment=comment,
        )
        upsert_feedback = getattr(self._repository, "upsert_feedback", None)
        if upsert_feedback is not None:
            upsert_feedback(
                message_id=message_id,
                user_id=user_id,
                rating=normalized_rating,
                comment=comment,
                created_at=created_at,
            )
        else:
            self._message_feedback[(message_id, user_id)] = result
        return result

    def _ensure_conversation_mutable(self, conversation_id: str) -> ConversationRecord:
        conversation = self._repository.require(conversation_id)
        if conversation_id in self._active_generations:
            raise BusinessError(
                ErrorCode.MESSAGE_ALREADY_RUNNING,
                details={"conversationId": conversation_id},
            )
        if conversation.status != ConversationStatus.ACTIVE:
            raise BusinessError(
                ErrorCode.CONVERSATION_ARCHIVED,
                details={"conversationId": conversation_id},
            )
        return conversation

    def is_message_cancelled(self, message_id: str) -> bool:
        for active in self._active_generations.values():
            if active.assistant_message_id == message_id and active.cancel_event.is_set():
                return True
        return False

    async def send_message(
        self,
        *,
        conversation_id: str,
        content: str,
        model_id: str | None = None,
        knowledge_base_ids: list[str] | None = None,
    ) -> SendMessageResult:
        normalized_content = content.strip()
        if not normalized_content:
            raise BusinessError(ErrorCode.MESSAGE_EMPTY)
        if len(normalized_content) > MAX_MESSAGE_LENGTH:
            raise BusinessError(ErrorCode.MESSAGE_TOO_LONG)

        async with self._lock:
            conversation = self._repository.require(conversation_id)
            if conversation.status != ConversationStatus.ACTIVE:
                raise BusinessError(ErrorCode.CONVERSATION_ARCHIVED)
            if conversation_id in self._active_generations:
                raise BusinessError(ErrorCode.MESSAGE_ALREADY_RUNNING)

            user_message = MessageRecord(
                id=new_message_id(),
                role=MessageRole.USER,
                content=normalized_content,
                status=MessageStatus.COMPLETED,
            )
            kb_ids = self._resolve_kb_ids(
                conversation_id,
                conversation,
                knowledge_base_ids,
            )
            retrieval_query = self._build_retrieval_query(conversation, normalized_content)
            hits = await self._retrieve_citations(kb_ids, retrieval_query)
            citation_payload, sources_payload = self._prepare_retrieval_payload(hits)
            openai_model = self._chat.resolve_model(model_id)
            conversation = self._load_conversation_for_generation(conversation_id)
            chat_messages = self._build_chat_messages(
                conversation,
                citations=citation_payload,
            )
            chat_messages.append({"role": "user", "content": normalized_content})
            chat_messages = append_citation_figures_to_messages(
                chat_messages,
                citations=citation_payload,
                upload_root=self._settings.upload_root_resolved,
                settings=self._settings,
            )
            try:
                reply, usage = await self._chat.complete(messages=chat_messages, model=openai_model)
            except OpenAIChatError as exc:
                raise BusinessError(
                    ErrorCode.MESSAGE_GENERATION_FAILED,
                    details={"reason": exc.code, "message": exc.message},
                ) from exc

            reply = self._finalize_assistant_markdown(reply)

            assistant_message = MessageRecord(
                id=new_message_id(),
                role=MessageRole.ASSISTANT,
                content=reply,
                status=MessageStatus.COMPLETED,
                citations=citation_payload,
                sources=sources_payload if citation_payload else None,
                usage=usage,
            )
            self._repository.add_messages(conversation_id, [user_message, assistant_message])

        return SendMessageResult(user_message=user_message, assistant_message=assistant_message)

    async def stream_message(
        self,
        *,
        conversation_id: str,
        content: str,
        model_id: str,
        knowledge_base_ids: list[str] | None = None,
    ) -> AsyncIterator[SseEvent]:
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("MESSAGE_EMPTY")
        if len(normalized_content) > MAX_MESSAGE_LENGTH:
            raise ValueError("MESSAGE_TOO_LONG")

        async with self._lock:
            conversation = self._repository.require(conversation_id)
            if conversation.status != ConversationStatus.ACTIVE:
                raise ValueError("CONVERSATION_ARCHIVED")
            if conversation_id in self._active_generations:
                raise MessageAlreadyRunningError()

            kb_ids = self._resolve_kb_ids(
                conversation_id,
                conversation,
                knowledge_base_ids,
            )
            user_message = MessageRecord(
                id=new_message_id(),
                role=MessageRole.USER,
                content=normalized_content,
                status=MessageStatus.COMPLETED,
            )
            assistant_message = MessageRecord(
                id=new_message_id(),
                role=MessageRole.ASSISTANT,
                content="",
                status=MessageStatus.STREAMING,
            )
            self._repository.add_messages(conversation_id, [user_message, assistant_message])

            cancel_event = asyncio.Event()
            self._active_generations[conversation_id] = ActiveGeneration(
                assistant_message_id=assistant_message.id,
                cancel_event=cancel_event,
            )

        yield SseEvent(
            event="message.created",
            data={
                "conversationId": conversation_id,
                "userMessageId": user_message.id,
                "assistantMessageId": assistant_message.id,
            },
        )

        hits: list[SearchHit] = []
        sources_payload: dict[str, Any] | None = None
        conversation_for_llm = self._load_conversation_for_generation(conversation_id)
        retrieval_query = self._build_retrieval_query(
            conversation_for_llm,
            normalized_content,
        )
        if kb_ids:
            yield SseEvent(
                event="retrieval.started",
                data={"messageId": assistant_message.id, "knowledgeBaseIds": kb_ids},
            )
            hits = await self._retrieve_citations(kb_ids, retrieval_query)
            citation_payload, sources_payload = self._prepare_retrieval_payload(hits)
            yield SseEvent(
                event="retrieval.completed",
                data={
                    "messageId": assistant_message.id,
                    "citations": citation_payload,
                    "sources": sources_payload,
                },
            )
        else:
            citation_payload = []

        openai_model = self._chat.resolve_model(model_id)
        chat_messages = self._build_chat_messages(
            conversation_for_llm,
            citations=citation_payload,
        )
        chat_messages = append_citation_figures_to_messages(
            chat_messages,
            citations=citation_payload,
            upload_root=self._settings.upload_root_resolved,
            settings=self._settings,
        )

        cancelled = False
        usage = MessageUsage()
        try:
            async for delta, usage_update in self._chat.stream(
                messages=chat_messages,
                model=openai_model,
                cancel_event=cancel_event,
            ):
                if cancel_event.is_set():
                    cancelled = True
                if usage_update is not None:
                    usage = usage_update
                    break
                if cancelled:
                    break
                if not delta:
                    continue
                async for delta_event in self._emit_character_deltas(
                    assistant_message=assistant_message,
                    text=delta,
                ):
                    yield delta_event
        except OpenAIChatError as exc:
            assistant_message.status = MessageStatus.FAILED
            self._persist_message_update(assistant_message)
            yield SseEvent(
                event="message.failed",
                data={
                    "code": exc.code,
                    "message": exc.message,
                    "details": {"conversationId": conversation_id},
                },
            )
            yield SseEvent(event="done", data={})
            async with self._lock:
                self._active_generations.pop(conversation_id, None)
            return

        assistant_message.citations = citation_payload
        assistant_message.sources = sources_payload
        assistant_message.usage = usage
        if not cancelled and assistant_message.content:
            assistant_message.content = self._finalize_assistant_markdown(
                assistant_message.content
            )

        if cancelled:
            assistant_message.status = MessageStatus.CANCELLED
            self._persist_message_update(assistant_message)
            yield SseEvent(
                event="message.completed",
                data={
                    "messageId": assistant_message.id,
                    "status": MessageStatus.CANCELLED.value,
                    "content": assistant_message.content,
                    "contentFormat": "markdown",
                    "citations": citation_payload,
                    "sources": sources_payload,
                },
            )
        else:
            assistant_message.status = MessageStatus.COMPLETED
            self._persist_message_update(assistant_message)
            yield SseEvent(
                event="usage.completed",
                data={"messageId": assistant_message.id, "usage": usage.to_api()},
            )
            yield SseEvent(
                event="message.completed",
                data={
                    "messageId": assistant_message.id,
                    "status": MessageStatus.COMPLETED.value,
                    "content": assistant_message.content,
                    "contentFormat": "markdown",
                    "citations": citation_payload,
                    "sources": sources_payload,
                },
            )

        yield SseEvent(event="done", data={})

        async with self._lock:
            self._active_generations.pop(conversation_id, None)

    async def cancel_message(self, *, conversation_id: str, message_id: str) -> dict[str, str]:
        async with self._lock:
            self._repository.require(conversation_id)
            message = self._find_message(conversation_id, message_id)
            if message is None:
                raise MessageNotFoundError(message_id)
            if message.role != MessageRole.ASSISTANT:
                raise MessageNotCancellableError(message_id, message.status)

            if message.status == MessageStatus.CANCELLED:
                return {"messageId": message_id, "status": MessageStatus.CANCELLED.value}

            if message.status in {MessageStatus.COMPLETED, MessageStatus.FAILED}:
                raise MessageNotCancellableError(message_id, message.status)

            active = self._active_generations.get(conversation_id)
            if active is not None and active.assistant_message_id == message_id:
                active.cancel_event.set()
                return {"messageId": message_id, "status": MessageStatus.CANCELLED.value}

            message.status = MessageStatus.CANCELLED
            self._persist_message_update(message)
            return {"messageId": message_id, "status": MessageStatus.CANCELLED.value}

    def _load_conversation_for_generation(self, conversation_id: str) -> ConversationRecord:
        return self._repository.require(conversation_id)

    def _persist_message_update(self, message: MessageRecord) -> None:
        try:
            self._repository.update_message(
                message.id,
                content=message.content,
                status=message.status,
                citations=message.citations,
                sources=message.sources,
                usage=message.usage,
            )
        except LookupError:
            logger.warning("Failed to persist message update: message_id=%s", message.id)

    def _build_retrieval_query(
        self,
        conversation: ConversationRecord,
        current_content: str,
    ) -> str:
        user_texts: list[str] = []
        for message in conversation.messages:
            if message.role != MessageRole.USER:
                continue
            text = message.content.strip()
            if text:
                user_texts.append(text)

        current = current_content.strip()
        if not user_texts or user_texts[-1] != current:
            user_texts.append(current)

        max_turns = self._settings.CHAT_HISTORY_MAX_TURNS
        if max_turns > 0:
            user_texts = user_texts[-max_turns:]
        return "\n".join(user_texts)

    def _select_history_messages(self, conversation: ConversationRecord) -> list[MessageRecord]:
        max_turns = self._settings.CHAT_HISTORY_MAX_TURNS
        if max_turns <= 0:
            return list(conversation.messages)
        max_messages = max_turns * 2
        if len(conversation.messages) <= max_messages:
            return list(conversation.messages)
        return list(conversation.messages[-max_messages:])

    def _resolve_kb_ids(
        self,
        conversation_id: str,
        conversation: ConversationRecord,
        knowledge_base_ids: list[str] | None,
    ) -> list[str]:
        if knowledge_base_ids:
            self._repository.bind_knowledge_bases(conversation_id, knowledge_base_ids)
            return list(knowledge_base_ids)
        return list(conversation.knowledge_base_ids)

    def _find_message(self, conversation_id: str, message_id: str) -> MessageRecord | None:
        conversation = self._repository.get(conversation_id)
        if conversation is None:
            return None
        for message in conversation.messages:
            if message.id == message_id:
                return message
        return None

    async def _retrieve_citations(self, knowledge_base_ids: list[str], query: str) -> list[SearchHit]:
        if not knowledge_base_ids or self._knowledge_base_service is None:
            return []
        kb_service = self._knowledge_base_service
        hits: list[SearchHit] = []
        for kb_identifier in knowledge_base_ids:
            kb_hits = await asyncio.to_thread(
                _search_kb_citations,
                kb_service,
                kb_identifier,
                query,
            )
            hits.extend(kb_hits)
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[: RAG_TOP_K * max(len(knowledge_base_ids), 1)]

    def _prepare_retrieval_payload(
        self,
        hits: list[SearchHit],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if not hits:
            return [], None
        citations = citations_from_hits(hits)
        sources = build_message_sources(
            citations,
            kb_service=self._knowledge_base_service,
        )
        return citations, sources

    async def _emit_character_deltas(
        self,
        *,
        assistant_message: MessageRecord,
        text: str,
    ) -> AsyncIterator[SseEvent]:
        """将模型 chunk 拆成单字 SSE，便于前端打字机效果。"""
        for char in text:
            assistant_message.content += char
            yield SseEvent(
                event="message.delta",
                data={"messageId": assistant_message.id, "delta": char},
            )
            await asyncio.sleep(0)

    @staticmethod
    def _finalize_assistant_markdown(content: str) -> str:
        """将回答中的 kb_images/ 等相对路径改写为可请求的静态资源 URL。"""
        return rewrite_markdown_asset_urls(content)

    @staticmethod
    def _citation_context_body(citation: dict[str, Any]) -> str:
        return str(citation.get("markdown") or citation.get("snippet") or "")

    @staticmethod
    def _markdown_response_instructions(citations: list[dict[str, Any]]) -> str:
        lines = [
            "Format your entire answer in Markdown (headings, lists, and emphasis when helpful).",
            "When stating facts from a source, append the source number in square brackets "
            "like [1] at the end of the relevant sentence.",
            "If the context is insufficient, say so clearly.",
            "When illustrating with an image from the sources, embed it using Markdown image "
            "syntax with the exact assetUrl listed below—do not invent or alter paths.",
        ]
        figure_lines: list[str] = []
        for citation in citations:
            index = citation.get("index")
            for asset in citation.get("imageAssets") or []:
                asset_url = asset.get("assetUrl")
                if not asset_url:
                    continue
                figure_lines.append(
                    f"- Source [{index}]: ![brief caption]({asset_url})"
                )
        if figure_lines:
            lines.append("Available images (copy assetUrl verbatim):")
            lines.extend(figure_lines)
        return "\n".join(lines) + "\n"

    def _build_chat_messages(
        self,
        conversation: ConversationRecord,
        *,
        citations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if citations:
            blocks: list[str] = []
            has_figures = any(
                citation.get("storageKey") or citation.get("imageAssets")
                for citation in citations
            )
            for citation in citations:
                page = citation.get("page")
                page_suffix = f" 第{page}页" if page is not None else ""
                file_name = citation.get("fileName") or ""
                figure_note = ""
                if citation.get("storageKey") or citation.get("imageAssets"):
                    figure_note = "（含图示，回答中可用 Markdown 嵌入下方 assetUrl）"
                blocks.append(
                    f"来源[{citation['index']}]（文件：{file_name}{page_suffix}{figure_note}）:\n"
                    f"{self._citation_context_body(citation)}"
                )
            context = "\n\n".join(blocks)
            vision_note = ""
            if has_figures and self._settings.CHAT_VISION_ENABLED:
                vision_note = (
                    "Some sources include figures; follow-up user content may attach those images. "
                    "Combine text and figures in your Markdown answer, cite with [n], and embed "
                    "images using the assetUrl list when showing them to the user.\n"
                )
            elif has_figures:
                vision_note = (
                    "Some sources include figures; embed them in Markdown using the listed assetUrl "
                    "when they help answer the question, and cite with [n].\n"
                )
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant. Answer using the numbered sources below.\n"
                        f"{self._markdown_response_instructions(citations)}"
                        f"{vision_note}"
                        f"Context:\n{context}"
                    ),
                }
            )
        for message in self._select_history_messages(conversation):
            if message.role == MessageRole.ASSISTANT and message.status == MessageStatus.STREAMING:
                continue
            if message.role not in {MessageRole.USER, MessageRole.ASSISTANT}:
                continue
            if not message.content.strip():
                continue
            messages.append({"role": message.role.value, "content": message.content})
        return messages
