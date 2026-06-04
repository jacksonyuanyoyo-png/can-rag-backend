from __future__ import annotations

from app.services.rag.vlm_text_quality import (
    is_acceptable_vlm_description,
    is_refusal_like_vlm_text,
)


def test_refusal_phrases_detected() -> None:
    assert is_refusal_like_vlm_text("请上传或提供具体的文档页面图片内容，我将帮助提取。")
    assert not is_acceptable_vlm_description(
        "请上传或提供具体的文档页面图片内容，我将帮助提取。"
    )


def test_acceptable_description() -> None:
    text = "- 左侧菜单包含问答与知识库模块，当前选中公共知识库。"
    assert is_acceptable_vlm_description(text)
    assert not is_refusal_like_vlm_text(text)
