from __future__ import annotations

from app.services.openai_chat_service import _completion_limit_kwargs


def test_completion_limit_kwargs_uses_max_tokens_for_gpt4() -> None:
    assert _completion_limit_kwargs("gpt-4o-mini", 4096) == {"max_tokens": 4096}


def test_completion_limit_kwargs_uses_max_completion_tokens_for_gpt5() -> None:
    assert _completion_limit_kwargs("gpt-5", 4096) == {"max_completion_tokens": 4096}


def test_completion_limit_kwargs_uses_max_completion_tokens_for_o_series() -> None:
    assert _completion_limit_kwargs("o3-mini", 2048) == {"max_completion_tokens": 2048}
