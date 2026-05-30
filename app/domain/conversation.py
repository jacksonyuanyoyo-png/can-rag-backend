from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from app.domain.knowledge_base import utc_now_iso


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MessageStatus(StrEnum):
    PENDING = "pending"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


def new_message_id() -> str:
    return f"msg_{uuid4().hex[:12]}"


@dataclass(slots=True)
class MessageUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def to_api(self) -> dict[str, int]:
        return {
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "totalTokens": self.total_tokens,
        }


@dataclass(slots=True)
class MessageRecord:
    id: str
    role: MessageRole
    content: str
    status: MessageStatus = MessageStatus.COMPLETED
    citations: list[dict[str, Any]] = field(default_factory=list)
    usage: MessageUsage | None = None
    created_at: str = field(default_factory=utc_now_iso)
    edited_at: str | None = None

    def to_api(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "role": self.role.value,
            "content": self.content,
            "createdAt": self.created_at,
            "editedAt": self.edited_at,
        }
        if self.role == MessageRole.ASSISTANT:
            payload["status"] = self.status.value
            payload["citations"] = self.citations
            if self.usage is not None:
                payload["usage"] = self.usage.to_api()
        return payload


@dataclass(slots=True)
class ConversationRecord:
    id: str
    title: str = "New chat"
    folder: str | None = None
    pinned: bool = False
    knowledge_base_ids: list[str] = field(default_factory=list)
    status: ConversationStatus = ConversationStatus.ACTIVE
    messages: list[MessageRecord] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def touch(self) -> None:
        self.updated_at = utc_now_iso()

    def to_list_api(self) -> dict[str, Any]:
        preview = self.messages[-1].content if self.messages else ""
        return {
            "id": self.id,
            "title": self.title,
            "updatedAt": self.updated_at,
            "messageCount": len(self.messages),
            "preview": preview,
            "pinned": self.pinned,
            "folder": self.folder,
        }
