from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from app.domain.conversation import MessageUsage


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            elif part.get("type") == "image_url":
                parts.append("[image]")
        return " ".join(parts)
    return str(content)


def _last_user_content(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return _content_to_text(message.get("content", ""))
    return ""


class FakeOpenAIChatService:
    """测试用 OpenAI 替身，避免单元测试访问真实 API。"""

    def resolve_model(self, model_id: str | None) -> str:
        return (model_id or "").strip() or "gpt-4o-mini"

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
    ) -> tuple[str, MessageUsage]:
        user = _last_user_content(messages)
        return f"Mock reply: {user}", MessageUsage(prompt_tokens=3, completion_tokens=7, total_tokens=10)

    async def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[tuple[str, MessageUsage | None]]:
        text = f"Mock reply: {_last_user_content(messages)}"
        chunk_size = 4
        for index in range(0, len(text), chunk_size):
            if cancel_event is not None and cancel_event.is_set():
                yield "", MessageUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5)
                return
            yield text[index : index + chunk_size], None
            await asyncio.sleep(0.03)
        yield "", MessageUsage(prompt_tokens=3, completion_tokens=7, total_tokens=10)
