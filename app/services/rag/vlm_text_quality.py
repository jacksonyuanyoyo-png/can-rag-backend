from __future__ import annotations

import re

_MIN_DESCRIPTION_CHARS = 24

_REFUSAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"请上传",
        r"提供.{0,12}图片",
        r"无法识别",
        r"无法处理",
        r"无法看清",
        r"看不清楚",
        r"不能识别",
        r"拒绝",
        r"抱歉.{0,8}无法",
        r"抱歉.{0,8}不能",
        r"需要.{0,12}更清晰",
        r"请提供.{0,12}文档",
        r"没有.{0,8}图片",
        r"未.{0,4}提供.{0,8}图片",
        r"^我无法",
        r"^我不能",
        r"I cannot",
        r"I can't",
        r"unable to (view|see|process)",
    )
)


def is_refusal_like_vlm_text(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    normalized = stripped.casefold()
    for pattern in _REFUSAL_PATTERNS:
        if pattern.search(normalized):
            return True
    return False


def is_acceptable_vlm_description(text: str | None) -> bool:
    if text is None:
        return False
    stripped = text.strip()
    if len(stripped) < _MIN_DESCRIPTION_CHARS:
        return False
    return not is_refusal_like_vlm_text(stripped)
