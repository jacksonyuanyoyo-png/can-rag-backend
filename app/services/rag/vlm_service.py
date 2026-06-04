from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openai import APIError, APIStatusError, AuthenticationError, OpenAI, RateLimitError

from app.core.config import Settings
from app.services.rag.image_normalize import normalize_image_bytes_for_vision

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你是文档图片理解助手，请将图片内容转为结构化中文文本："
    "若是流程图输出有序步骤，若是表格输出 markdown 表格，否则输出要点列表；只输出内容本身"
)

_PAGE_MARKDOWN_SYSTEM_PROMPT = (
    "你是文档 OCR 助手。请将页面图片中的全部可见文字、键位标签、表格与图示说明转为 Markdown。"
    "适用于键盘说明书、产品手册、布局图。保留标题层级、列表、表格（markdown table）。"
    "图示区域用 ![描述](placeholder) 占位。"
    "必须输出完整正文，不得说这是照片、不得拒答、不得要求更清晰图片；只输出 Markdown，不要解释"
)

_PDF_PAGE_MARKDOWN_SYSTEM_PROMPT = (
    "你是文档 OCR 助手。请将 PDF 页面转换为 Markdown："
    "保留标题层级、列表、表格（markdown table）、键位/布局说明；"
    "图片区域用 ![描述](placeholder) 占位；"
    "必须输出完整 Markdown 正文，不得拒绝、不得只回复无法处理或询问用户需求；"
    "不要代码围栏，不要解释或前后缀"
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
        force: bool = False,
        model: str | None = None,
    ) -> str | None:
        if not force and not self._settings.VLM_ENABLED:
            return None
        size = len(image_bytes)
        min_bytes = 1 if force else self._settings.VLM_MIN_IMAGE_BYTES
        if size < min_bytes:
            logger.debug(
                "vlm skip: image too small (%d < %d bytes)",
                size,
                min_bytes,
            )
            return None
        if size > self._settings.VLM_MAX_IMAGE_BYTES:
            logger.debug(
                "vlm skip: image too large (%d > %d bytes)",
                size,
                self._settings.VLM_MAX_IMAGE_BYTES,
            )
            return None
        try:
            safe_bytes, safe_mime = normalize_image_bytes_for_vision(
                image_bytes,
                suffix_hint=mime_type.removeprefix("image/"),
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("vlm skip: image normalize failed: %s", exc)
            return None
        messages = _build_messages(safe_bytes, mime_type=safe_mime, hint=hint)
        return self._invoke(messages, model=model)

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
        model: str | None = None,
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
        try:
            safe_bytes, safe_mime = normalize_image_bytes_for_vision(
                image_bytes,
                suffix_hint=mime_type.removeprefix("image/"),
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("vlm page markdown skip: normalize failed: %s", exc)
            return ""
        messages = _build_page_markdown_messages(
            safe_bytes,
            mime_type=safe_mime,
            page_number=page_number,
        )
        resolved = model or (self._settings.PDF_VLM_MODEL if force else None)
        return self._invoke(messages, model=resolved)

    def pdf_page_to_markdown(
        self,
        pdf_bytes: bytes,
        *,
        page_number: int,
        filename: str = "page.pdf",
        force: bool = False,
    ) -> str:
        if not force and not self._settings.VLM_ENABLED:
            return ""
        size = len(pdf_bytes)
        min_bytes = 1 if force else self._settings.VLM_MIN_IMAGE_BYTES
        if size < min_bytes:
            logger.debug(
                "vlm pdf page markdown skip: pdf too small (%d < %d bytes)",
                size,
                min_bytes,
            )
            return ""
        if size > self._settings.VLM_MAX_PDF_BYTES:
            logger.debug(
                "vlm pdf page markdown skip: pdf too large (%d > %d bytes)",
                size,
                self._settings.VLM_MAX_PDF_BYTES,
            )
            return ""
        messages = _build_pdf_page_markdown_messages(
            pdf_bytes,
            filename=filename,
            page_number=page_number,
        )
        return self._invoke(messages, model=self._settings.PDF_VLM_MODEL)

    def _invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> str:
        if self._chat_completion is not None:
            return self._chat_completion(messages)
        client = self._client_or_raise()
        resolved_model = model or self._settings.VLM_MODEL
        try:
            response = client.chat.completions.create(
                model=resolved_model,
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


def _build_pdf_page_markdown_messages(
    pdf_bytes: bytes,
    *,
    filename: str,
    page_number: int,
) -> list[dict[str, Any]]:
    encoded = base64.standard_b64encode(pdf_bytes).decode("ascii")
    file_data = f"data:application/pdf;base64,{encoded}"
    return [
        {"role": "system", "content": _PDF_PAGE_MARKDOWN_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "file",
                    "file": {
                        "filename": filename,
                        "file_data": file_data,
                    },
                },
                {
                    "type": "text",
                    "text": f"请将第 {page_number} 页转换为 Markdown。",
                },
            ],
        },
    ]


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
