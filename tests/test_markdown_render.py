from __future__ import annotations

from app.services.markdown_render import (
    is_markdown_content,
    markdown_payload_for_storage_text,
    rewrite_markdown_asset_urls,
)


def test_rewrite_markdown_asset_urls() -> None:
    text = "说明 ![图](kb_images/a.png) 结束"
    md = rewrite_markdown_asset_urls(text)
    assert md.startswith("说明 ![图](/v1/uploads/assets/kb_images/a.png)")


def test_markdown_payload_for_docx_chunk() -> None:
    text = "标题\n\n![图示](kb_images/x.jpeg)\n\n正文"
    payload = markdown_payload_for_storage_text(text)
    assert payload["textFormat"] == "markdown"
    assert payload["hasImages"] is True
    assert "/v1/uploads/assets/kb_images/x.jpeg" in payload["markdown"]


def test_plain_text_stays_plain() -> None:
    payload = markdown_payload_for_storage_text("Hello only ascii")
    assert payload["textFormat"] == "plain"
    assert payload["markdown"] == "Hello only ascii"
    assert payload["hasImages"] is False


def test_is_markdown_detects_headings() -> None:
    assert is_markdown_content("## Section\n\nbody") is True
