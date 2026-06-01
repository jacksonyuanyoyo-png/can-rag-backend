from __future__ import annotations

from typing import Any

_NUL = "\x00"


def sanitize_pg_text(value: str) -> str:
    """PostgreSQL text/varchar 不允许 NUL (0x00)，解析 PDF/DOCX 等可能带入。"""
    if not value:
        return value
    if _NUL not in value:
        return value
    return value.replace(_NUL, "")


def sanitize_for_postgres_json(value: Any) -> Any:
    """递归清理写入 jsonb 的字符串中的 NUL。"""
    if isinstance(value, str):
        return sanitize_pg_text(value)
    if isinstance(value, dict):
        return {key: sanitize_for_postgres_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_for_postgres_json(item) for item in value]
    return value
