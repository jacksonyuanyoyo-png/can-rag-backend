from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from openai import APIError, APIStatusError, AsyncOpenAI, AuthenticationError, RateLimitError

from app.core.config import Settings
from app.domain.conversation import MessageUsage

_OPENAI_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
# 新一代模型（如 gpt-5、o 系列）使用 max_completion_tokens，不再接受 max_tokens
_MAX_COMPLETION_TOKENS_PREFIXES = ("gpt-5", "o1", "o3", "o4", "chatgpt-5")


class OpenAIChatError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class OpenAIChatService:
    """OpenAI Chat Completions（同步与流式）。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncOpenAI | None = None

    def resolve_model(self, model_id: str | None) -> str:
        candidate = (model_id or "").strip() or self._settings.OPENAI_CHAT_MODEL
        lowered = candidate.casefold()
        if any(lowered.startswith(prefix) for prefix in _OPENAI_MODEL_PREFIXES):
            return candidate
        return self._settings.OPENAI_CHAT_MODEL

    def _client_or_raise(self) -> AsyncOpenAI:
        if not self._settings.OPENAI_API_KEY.strip():
            raise OpenAIChatError("OPENAI_NOT_CONFIGURED", "OPENAI_API_KEY is not configured")
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._settings.OPENAI_API_KEY,
                base_url=self._settings.OPENAI_BASE_URL.rstrip("/"),
                timeout=self._settings.HTTP_TIMEOUT_SECONDS,
            )
        return self._client

    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
    ) -> tuple[str, MessageUsage]:
        client = self._client_or_raise()
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                **_completion_limit_kwargs(model, self._settings.DEFAULT_MAX_TOKENS),
            )
        except AuthenticationError as exc:
            raise OpenAIChatError("OPENAI_AUTH_ERROR", str(exc)) from exc
        except RateLimitError as exc:
            raise OpenAIChatError("OPENAI_RATE_LIMITED", str(exc)) from exc
        except APIStatusError as exc:
            raise OpenAIChatError("OPENAI_API_ERROR", str(exc)) from exc
        except APIError as exc:
            raise OpenAIChatError("OPENAI_API_ERROR", str(exc)) from exc

        choice = response.choices[0] if response.choices else None
        content = (choice.message.content or "") if choice and choice.message else ""
        return content, _usage_from_response(response.usage)

    async def stream(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncIterator[tuple[str, MessageUsage | None]]:
        client = self._client_or_raise()
        usage: MessageUsage | None = None
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                **_completion_limit_kwargs(model, self._settings.DEFAULT_MAX_TOKENS),
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                if cancel_event is not None and cancel_event.is_set():
                    break
                if chunk.usage is not None:
                    usage = _usage_from_response(chunk.usage)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta, None
        except AuthenticationError as exc:
            raise OpenAIChatError("OPENAI_AUTH_ERROR", str(exc)) from exc
        except RateLimitError as exc:
            raise OpenAIChatError("OPENAI_RATE_LIMITED", str(exc)) from exc
        except APIStatusError as exc:
            raise OpenAIChatError("OPENAI_API_ERROR", str(exc)) from exc
        except APIError as exc:
            raise OpenAIChatError("OPENAI_API_ERROR", str(exc)) from exc

        if usage is None:
            usage = MessageUsage()
        yield "", usage


def _completion_limit_kwargs(model: str, limit: int) -> dict[str, int]:
    lowered = model.casefold()
    if any(lowered.startswith(prefix) for prefix in _MAX_COMPLETION_TOKENS_PREFIXES):
        return {"max_completion_tokens": limit}
    return {"max_tokens": limit}


def _usage_from_response(usage: Any) -> MessageUsage:
    if usage is None:
        return MessageUsage()
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or 0)
    if total == 0:
        total = prompt + completion
    return MessageUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )
