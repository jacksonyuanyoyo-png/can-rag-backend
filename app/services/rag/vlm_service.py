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

_PAGE_MARKDOWN_SYSTEM_PROMPT = (
    "你是文档 OCR 助手。请将 PDF 页面图片转换为 Markdown："
    "保留标题层级、列表、表格（markdown table）、键位/布局说明；"
    "图片区域用 ![描述](placeholder) 占位；只输出 Markdown，不要解释或前后缀"
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

    def page_to_markdown(
        self,
        image_bytes: bytes,
        *,
        page_number: int,
        mime_type: str = "image/png",
        force: bool = False,
    ) -> str:
        if not force and not self._settings.VLM_ENABLED:
            return ""
        size = len(image_bytes)
        min_bytes = 1 if force else self._settings.VLM_MIN_IMAGE_BYTES
        if size < min_bytes:
            logger.debug(
                "vlm page markdown skip: image too small (%d < %d bytes)",
                size,
                min_bytes,
            )
            return ""
        if size > self._settings.VLM_MAX_IMAGE_BYTES:
            logger.debug(
                "vlm page markdown skip: image too large (%d > %d bytes)",
                size,
                self._settings.VLM_MAX_IMAGE_BYTES,
            )
            return ""
        messages = _build_page_markdown_messages(
            image_bytes,
            mime_type=mime_type,
            page_number=page_number,
        )
        return self._invoke(messages)

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


def _build_page_markdown_messages(
    image_bytes: bytes,
    *,
    mime_type: str,
    page_number: int,
) -> list[dict[str, Any]]:
    encoded = base64.standard_b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{encoded}"
    return [
        {"role": "system", "content": _PAGE_MARKDOWN_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"请将第 {page_number} 页转换为 Markdown。",
                },
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


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
