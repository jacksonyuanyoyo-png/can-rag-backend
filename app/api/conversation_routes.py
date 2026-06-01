from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.common import get_request_id, paginated_response, success_response
from app.api.http_common import SSE_STREAM_HEADERS, SSE_STREAM_PREAMBLE, format_sse_event
from app.api.schemas.conversation import (
    CreateConversationRequest,
    MessageFeedbackRequest,
    SendMessageRequest,
    SendMessageResponse,
    UpdateConversationRequest,
)
from app.core.errors import BusinessError, ErrorCode
from app.domain.conversation import MessageRole
from app.repositories.conversation_repository import ConversationNotFoundError
from app.services.conversation_service import (
    ConversationService,
    MessageAlreadyRunningError,
    MessageNotCancellableError,
    MessageNotFoundError,
)


conversation_router = APIRouter(prefix="/v1", tags=["Conversations"])


class SendMessageStreamRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    content: str
    model_id: str = Field(default="gpt-5", alias="modelId")
    knowledge_base_ids: list[str] = Field(default_factory=list, alias="knowledgeBaseIds")


def get_conversation_service(request: Request) -> ConversationService:
    service = getattr(request.app.state, "conversation_service", None)
    if service is None:
        raise RuntimeError("ConversationService is not initialized")
    return service


ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]


def _require_conversation(service: ConversationService, conversation_id: str):
    try:
        return service.require_conversation(conversation_id)
    except ConversationNotFoundError as exc:
        raise BusinessError(
            ErrorCode.CONVERSATION_NOT_FOUND,
            details={"conversationId": exc.conversation_id},
        ) from exc


def _message_list_item(message) -> dict[str, Any]:
    if message.role == MessageRole.ASSISTANT:
        return message.to_api()
    return {
        "id": message.id,
        "role": message.role.value,
        "content": message.content,
        "createdAt": message.created_at,
        "editedAt": message.edited_at,
    }


def _send_message_response(result) -> dict[str, Any]:
    usage = result.assistant_message.usage
    return SendMessageResponse(
        userMessage={
            "id": result.user_message.id,
            "role": result.user_message.role.value,
            "content": result.user_message.content,
            "createdAt": result.user_message.created_at,
        },
        assistantMessage={
            "id": result.assistant_message.id,
            "role": result.assistant_message.role.value,
            "content": result.assistant_message.content,
            "status": result.assistant_message.status.value,
            "createdAt": result.assistant_message.created_at,
            "citations": result.assistant_message.citations,
            "sources": result.assistant_message.sources,
            "usage": usage.to_api() if usage is not None else {
                "promptTokens": 0,
                "completionTokens": 0,
                "totalTokens": 0,
            },
        },
    ).model_dump()


@conversation_router.get("/conversations")
async def list_conversations(
    request: Request,
    service: ConversationServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=100)] = 20,
    q: str | None = None,
    folderId: str | None = None,
) -> dict[str, Any]:
    result = service.list_conversations(page=page, page_size=pageSize, q=q, folder_id=folderId)
    return paginated_response(
        data=result.items,
        page=result.page,
        page_size=result.page_size,
        total=result.total,
        request_id=get_request_id(request),
    )


@conversation_router.post("/conversations", status_code=201)
async def create_conversation(
    request: Request,
    body: CreateConversationRequest,
    service: ConversationServiceDep,
) -> dict[str, Any]:
    conversation = service.create_conversation(
        title=body.title,
        folder=body.folder,
        pinned=body.pinned,
    )
    return success_response(
        data=conversation.to_list_api(),
        request_id=get_request_id(request),
    )


@conversation_router.get("/conversations/{conversation_id}")
async def get_conversation(
    request: Request,
    conversation_id: str,
    service: ConversationServiceDep,
) -> dict[str, Any]:
    conversation = _require_conversation(service, conversation_id)
    return success_response(
        data=conversation.to_list_api(),
        request_id=get_request_id(request),
    )


@conversation_router.patch("/conversations/{conversation_id}")
async def update_conversation(
    request: Request,
    conversation_id: str,
    body: UpdateConversationRequest,
    service: ConversationServiceDep,
) -> dict[str, Any]:
    try:
        conversation = service.update_conversation(
            conversation_id,
            title=body.title if "title" in body.model_fields_set else None,
            folder=body.folder if "folder" in body.model_fields_set and body.folder is not None else None,
            pinned=body.pinned if "pinned" in body.model_fields_set else None,
            clear_folder="folder" in body.model_fields_set and body.folder is None,
        )
    except ConversationNotFoundError as exc:
        raise BusinessError(
            ErrorCode.CONVERSATION_NOT_FOUND,
            details={"conversationId": exc.conversation_id},
        ) from exc

    return success_response(
        data=conversation.to_list_api(),
        request_id=get_request_id(request),
    )


@conversation_router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    request: Request,
    conversation_id: str,
    service: ConversationServiceDep,
) -> dict[str, Any]:
    try:
        data = service.delete_conversation(conversation_id)
    except ConversationNotFoundError as exc:
        raise BusinessError(
            ErrorCode.CONVERSATION_NOT_FOUND,
            details={"conversationId": exc.conversation_id},
        ) from exc

    return success_response(data=data, request_id=get_request_id(request))


@conversation_router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    request: Request,
    conversation_id: str,
    service: ConversationServiceDep,
) -> dict[str, Any]:
    _require_conversation(service, conversation_id)
    messages = service.list_messages(conversation_id)
    return success_response(
        data=[_message_list_item(message) for message in messages],
        request_id=get_request_id(request),
    )


@conversation_router.post("/conversations/{conversation_id}/messages")
async def send_message(
    request: Request,
    conversation_id: str,
    body: SendMessageRequest,
    service: ConversationServiceDep,
) -> dict[str, Any]:
    _require_conversation(service, conversation_id)
    kb_ids = body.knowledge_base_ids or None
    result = await service.send_message(
        conversation_id=conversation_id,
        content=body.content,
        model_id=body.model_id,
        knowledge_base_ids=kb_ids,
    )
    return success_response(
        data=_send_message_response(result),
        request_id=get_request_id(request),
    )


async def _sse_stream(
    *,
    service: ConversationService,
    conversation_id: str,
    body: SendMessageStreamRequest,
) -> AsyncIterator[str]:
    yield SSE_STREAM_PREAMBLE
    try:
        async for event in service.stream_message(
            conversation_id=conversation_id,
            content=body.content,
            model_id=body.model_id,
            knowledge_base_ids=body.knowledge_base_ids,
        ):
            yield format_sse_event(event=event.event, data=event.data)
            await asyncio.sleep(0)
    except ConversationNotFoundError as exc:
        yield format_sse_event(
            event="message.failed",
            data={
                "code": "CONVERSATION_NOT_FOUND",
                "message": "Conversation not found",
                "details": {"conversationId": exc.conversation_id},
            },
        )
        yield format_sse_event(event="done", data={})
    except MessageAlreadyRunningError:
        yield format_sse_event(
            event="message.failed",
            data={
                "code": "MESSAGE_ALREADY_RUNNING",
                "message": "Another assistant message is still running",
                "details": {"conversationId": conversation_id},
            },
        )
        yield format_sse_event(event="done", data={})
    except ValueError as exc:
        code = str(exc)
        yield format_sse_event(
            event="message.failed",
            data={
                "code": code,
                "message": _validation_message(code),
                "details": {"conversationId": conversation_id},
            },
        )
        yield format_sse_event(event="done", data={})


def _validation_message(code: str) -> str:
    messages = {
        "MESSAGE_EMPTY": "Message content must not be empty",
        "MESSAGE_TOO_LONG": "Message content exceeds maximum length",
        "CONVERSATION_ARCHIVED": "Conversation is archived",
        "OPENAI_NOT_CONFIGURED": "OpenAI API key is not configured",
        "OPENAI_AUTH_ERROR": "OpenAI authentication failed",
        "OPENAI_RATE_LIMITED": "OpenAI rate limit exceeded",
        "OPENAI_API_ERROR": "OpenAI API request failed",
    }
    return messages.get(code, "Invalid request")


@conversation_router.post("/conversations/{conversation_id}/messages:stream")
async def stream_conversation_message(
    conversation_id: str,
    body: SendMessageStreamRequest,
    service: ConversationServiceDep,
) -> StreamingResponse:
    generator = _sse_stream(service=service, conversation_id=conversation_id, body=body)
    return StreamingResponse(
        generator,
        media_type="text/event-stream; charset=utf-8",
        headers=SSE_STREAM_HEADERS,
    )


@conversation_router.post("/conversations/{conversation_id}/messages/{message_id}:cancel")
async def cancel_conversation_message(
    request: Request,
    conversation_id: str,
    message_id: str,
    service: ConversationServiceDep,
) -> dict[str, Any]:
    try:
        data = await service.cancel_message(conversation_id=conversation_id, message_id=message_id)
    except ConversationNotFoundError as exc:
        raise BusinessError(
            ErrorCode.CONVERSATION_NOT_FOUND,
            details={"conversationId": exc.conversation_id},
        ) from exc
    except MessageNotFoundError as exc:
        raise BusinessError(
            ErrorCode.RESOURCE_NOT_FOUND,
            details={"messageId": exc.message_id, "conversationId": conversation_id},
        ) from exc
    except MessageNotCancellableError as exc:
        raise BusinessError(
            ErrorCode.MESSAGE_ALREADY_RUNNING,
            details={"messageId": exc.message_id, "status": exc.status.value},
        ) from exc

    return success_response(data=data, request_id=get_request_id(request))


@conversation_router.post("/messages/{message_id}/feedback")
async def message_feedback(
    request: Request,
    message_id: str,
    body: MessageFeedbackRequest,
    service: ConversationServiceDep,
) -> dict[str, Any]:
    try:
        result = service.message_feedback(
            message_id,
            rating=body.rating,
            comment=body.comment,
        )
    except MessageNotFoundError as exc:
        raise BusinessError(
            ErrorCode.RESOURCE_NOT_FOUND,
            details={"messageId": exc.message_id},
        ) from exc

    return success_response(
        data=result.to_api(),
        request_id=get_request_id(request),
    )
