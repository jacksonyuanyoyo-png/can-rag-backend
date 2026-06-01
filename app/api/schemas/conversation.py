from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateConversationRequest(BaseModel):
    title: str = "New chat"
    folder: str | None = None
    pinned: bool = False


class UpdateConversationRequest(BaseModel):
    title: str | None = None
    folder: str | None = None
    pinned: bool | None = None


class DeleteConversationResponse(BaseModel):
    success: bool = True


class MessageFeedbackRequest(BaseModel):
    rating: str
    comment: str | None = None


class ConversationListItem(BaseModel):
    id: str
    title: str
    updatedAt: str
    messageCount: int
    preview: str
    pinned: bool
    folder: str | None = None


class MessageItem(BaseModel):
    id: str
    role: str
    content: str
    createdAt: str
    editedAt: str | None = None


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    content: str = Field(min_length=1)
    model_id: str | None = Field(default=None, alias="modelId")
    knowledge_base_ids: list[str] = Field(default_factory=list, alias="knowledgeBaseIds")


class UserMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    createdAt: str


class UsageResponse(BaseModel):
    promptTokens: int
    completionTokens: int
    totalTokens: int


class AssistantMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    status: str
    createdAt: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    sources: dict[str, Any] | None = None
    usage: UsageResponse


class SendMessageResponse(BaseModel):
    userMessage: UserMessageResponse
    assistantMessage: AssistantMessageResponse
