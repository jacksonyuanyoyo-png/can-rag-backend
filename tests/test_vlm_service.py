from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.services.rag.vlm_service import VlmService, _SYSTEM_PROMPT


def _large_enough_payload() -> bytes:
    return b"x" * 5120


def test_describe_image_returns_none_when_disabled() -> None:
    calls: list[list[dict[str, Any]]] = []

    def fake_completion(messages: list[dict[str, Any]]) -> str:
        calls.append(messages)
        return "should not run"

    settings = Settings(VLM_ENABLED=False)
    service = VlmService(settings, chat_completion=fake_completion)

    result = service.describe_image(_large_enough_payload())

    assert result is None
    assert calls == []


def test_describe_image_uses_injected_completion_with_data_url() -> None:
    captured: list[list[dict[str, Any]]] = []

    def fake_completion(messages: list[dict[str, Any]]) -> str:
        captured.append(messages)
        return "结构化描述"

    settings = Settings(VLM_ENABLED=True)
    service = VlmService(settings, chat_completion=fake_completion)
    payload = _large_enough_payload()

    result = service.describe_image(payload, mime_type="image/png", hint="流程图")

    assert result == "结构化描述"
    assert len(captured) == 1
    messages = captured[0]
    system_content = messages[0]["content"]
    assert system_content == _SYSTEM_PROMPT
    user_content = messages[1]["content"]
    assert user_content[0]["text"] == "流程图"
    image_url = user_content[1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")
    encoded_part = image_url.split(",", 1)[1]
    assert base64.standard_b64decode(encoded_part) == payload


def test_describe_image_skips_when_below_min_bytes() -> None:
    calls: list[list[dict[str, Any]]] = []

    def fake_completion(messages: list[dict[str, Any]]) -> str:
        calls.append(messages)
        return "unused"

    settings = Settings(VLM_ENABLED=True, VLM_MIN_IMAGE_BYTES=5120)
    service = VlmService(settings, chat_completion=fake_completion)

    result = service.describe_image(b"tiny")

    assert result is None
    assert calls == []


def test_describe_image_file_reads_tmp_file(tmp_path: Path) -> None:
    captured: list[list[dict[str, Any]]] = []

    def fake_completion(messages: list[dict[str, Any]]) -> str:
        captured.append(messages)
        return "文件描述"

    image_path = tmp_path / "diagram.png"
    payload = _large_enough_payload()
    image_path.write_bytes(payload)

    settings = Settings(VLM_ENABLED=True)
    service = VlmService(settings, chat_completion=fake_completion)

    result = service.describe_image_file(image_path)

    assert result == "文件描述"
    assert len(captured) == 1
    image_url = captured[0][1]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")
