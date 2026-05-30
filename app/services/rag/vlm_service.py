from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openai import APIError, APIStatusError, AuthenticationError, OpenAI, RateLimitError

from app.core.config import Settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是文档图片理解助手，请将图片内容转为结构化中文文本："
    "若是流程图输出有序步骤，若是表格输出 markdown 表格，否则输出要点列表；只输出内容本身"
)

_SUFFIX_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

ChatCompletionFn = Callable[[list[dict[str, Any]]], str]


class VlmError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class VlmService:
    def __init__(
        self,
        settings: Settings,
        *,
        chat_completion: ChatCompletionFn | None = None,
    ) -> None:
        self._settings = settings
        self._chat_completion = chat_completion
        self._client: OpenAI | None = None

    def describe_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/png",
        hint: str | None = None,
    ) -> str | None:
        if not self._settings.VLM_ENABLED:
            return None
        size = len(image_bytes)
        if size < self._settings.VLM_MIN_IMAGE_BYTES:
            logger.debug(
                "vlm skip: image too small (%d < %d bytes)",
                size,
                self._settings.VLM_MIN_IMAGE_BYTES,
            )
            return None
        if size > self._settings.VLM_MAX_IMAGE_BYTES:
            logger.debug(
                "vlm skip: image too large (%d > %d bytes)",
                size,
                self._settings.VLM_MAX_IMAGE_BYTES,
            )
            return None
        messages = _build_messages(image_bytes, mime_type=mime_type, hint=hint)
        return self._invoke(messages)

    def describe_image_file(
        self,
        path: str | Path,
        *,
        hint: str | None = None,
    ) -> str | None:
        file_path = Path(path)
        image_bytes = file_path.read_bytes()
        mime_type = _mime_from_suffix(file_path.suffix)
        return self.describe_image(image_bytes, mime_type=mime_type, hint=hint)

    def _invoke(self, messages: list[dict[str, Any]]) -> str:
        if self._chat_completion is not None:
            return self._chat_completion(messages)
        client = self._client_or_raise()
        try:
            response = client.chat.completions.create(
                model=self._settings.VLM_MODEL,
                messages=messages,
            )
        except AuthenticationError as exc:
            raise VlmError("VLM_AUTH_ERROR", str(exc)) from exc
        except RateLimitError as exc:
            raise VlmError("VLM_RATE_LIMITED", str(exc)) from exc
        except APIStatusError as exc:
            raise VlmError("VLM_API_ERROR", str(exc)) from exc
        except APIError as exc:
            raise VlmError("VLM_API_ERROR", str(exc)) from exc

        choice = response.choices[0] if response.choices else None
        content = (choice.message.content or "") if choice and choice.message else ""
        return content.strip()

    def _client_or_raise(self) -> OpenAI:
        if not self._settings.OPENAI_API_KEY.strip():
            raise VlmError("VLM_NOT_CONFIGURED", "OPENAI_API_KEY is not configured")
        if self._client is None:
            self._client = OpenAI(
                api_key=self._settings.OPENAI_API_KEY,
                base_url=self._settings.OPENAI_BASE_URL.rstrip("/"),
                timeout=self._settings.VLM_TIMEOUT_SECONDS,
            )
        return self._client


def _build_messages(
    image_bytes: bytes,
    *,
    mime_type: str,
    hint: str | None,
) -> list[dict[str, Any]]:
    encoded = base64.standard_b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{encoded}"
    user_text = hint if hint else "请描述这张图片。"
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


def _mime_from_suffix(suffix: str) -> str:
    return _SUFFIX_MIME.get(suffix.lower(), "image/png")
