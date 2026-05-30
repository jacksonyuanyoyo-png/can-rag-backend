from __future__ import annotations

import uuid
from threading import Lock

from app.domain.conversation import ConversationRecord, ConversationStatus, MessageRecord
from app.domain.knowledge_base import utc_now_iso


class ConversationNotFoundError(LookupError):
    def __init__(self, conversation_id: str) -> None:
        super().__init__(f"Conversation not found: {conversation_id}")
        self.conversation_id = conversation_id


class ConversationRepository:
    """内存会话仓储。"""

    def __init__(self) -> None:
        self._conversations: dict[str, ConversationRecord] = {}
        self._lock = Lock()

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def list_all(self) -> list[ConversationRecord]:
        with self._lock:
            return [
                conversation
                for conversation in self._conversations.values()
                if conversation.status != ConversationStatus.DELETED
            ]

    def get(self, conversation_id: str) -> ConversationRecord | None:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status == ConversationStatus.DELETED:
                return None
            return conversation

    def require(self, conversation_id: str) -> ConversationRecord:
        conversation = self.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        return conversation

    def create(
        self,
        *,
        title: str,
        folder: str | None = None,
        pinned: bool = False,
    ) -> ConversationRecord:
        now = utc_now_iso()
        conversation = ConversationRecord(
            id=self._new_id("conv"),
            title=title,
            folder=folder,
            pinned=pinned,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._conversations[conversation.id] = conversation
        return conversation

    def add_messages(
        self,
        conversation_id: str,
        messages: list[MessageRecord],
    ) -> ConversationRecord:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status == ConversationStatus.DELETED:
                raise ConversationNotFoundError(conversation_id)
            conversation.messages.extend(messages)
            conversation.touch()
            return conversation

    def update(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        folder: str | None = None,
        pinned: bool | None = None,
        clear_folder: bool = False,
    ) -> ConversationRecord:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status == ConversationStatus.DELETED:
                raise ConversationNotFoundError(conversation_id)
            if title is not None:
                conversation.title = title
            if clear_folder:
                conversation.folder = None
            elif folder is not None:
                conversation.folder = folder
            if pinned is not None:
                conversation.pinned = pinned
            conversation.touch()
            return conversation

    def soft_delete(self, conversation_id: str) -> None:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status == ConversationStatus.DELETED:
                raise ConversationNotFoundError(conversation_id)
            conversation.status = ConversationStatus.DELETED
            conversation.touch()

    def bind_knowledge_bases(
        self,
        conversation_id: str,
        kb_ids: list[str],
    ) -> ConversationRecord:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status == ConversationStatus.DELETED:
                raise ConversationNotFoundError(conversation_id)
            conversation.knowledge_base_ids = list(dict.fromkeys(kb_ids))
            conversation.touch()
            return conversation

    def find_message_by_id(self, message_id: str) -> MessageRecord | None:
        with self._lock:
            for conversation in self._conversations.values():
                if conversation.status == ConversationStatus.DELETED:
                    continue
                for message in conversation.messages:
                    if message.id == message_id:
                        return message
            return None
