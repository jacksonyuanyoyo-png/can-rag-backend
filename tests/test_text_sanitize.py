from __future__ import annotations

from app.utils.text_sanitize import sanitize_for_postgres_json, sanitize_pg_text


def test_sanitize_pg_text_removes_nul() -> None:
    assert sanitize_pg_text("hello\x00world") == "helloworld"
    assert sanitize_pg_text("ok") == "ok"
    assert sanitize_pg_text("") == ""


def test_sanitize_for_postgres_json_nested() -> None:
    payload = {
        "file_name": "a\x00b.pdf",
        "nested": ["x\x00y", 1],
    }
    cleaned = sanitize_for_postgres_json(payload)
    assert cleaned["file_name"] == "ab.pdf"
    assert cleaned["nested"][0] == "xy"
    assert cleaned["nested"][1] == 1
