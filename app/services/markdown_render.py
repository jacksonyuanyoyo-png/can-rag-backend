from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from app.services.rag.parsing.md_parser import extract_image_storage_keys

_IMAGE_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def upload_asset_path(storage_key: str) -> str:
    """相对 API 路径，前端拼接 Base URL 即可请求图片。"""
    key = storage_key.strip().lstrip("/")
    encoded = "/".join(quote(part, safe="") for part in key.split("/"))
    return f"/v1/uploads/assets/{encoded}"


def is_markdown_content(text: str) -> bool:
    """启发式判断正文是否含 Markdown 结构（用于标注 textFormat）。"""
    if not text or not text.strip():
        return False
    if _IMAGE_REF_RE.search(text):
        return True
    if re.search(r"^#{1,6}\s+\S", text, re.MULTILINE):
        return True
    if "**" in text or re.search(r"^\s*[-*+]\s+\S", text, re.MULTILINE):
        return True
    if extract_image_storage_keys(text):
        return True
    return False


def rewrite_markdown_asset_urls(text: str) -> str:
    """将 Markdown 中的 kb_images/、kb/ 相对路径改写为可请求的 assets API 路径。

    前端若与后端同域或已代理 /v1，可直接用 react-markdown 渲染 markdown 字段；
    若需绝对 URL，在浏览器侧对 /v1/uploads/assets/ 前缀拼接 apiBase 即可。
    """
    if not text:
        return text

    def replace_image_ref(match: re.Match[str]) -> str:
        alt = match.group(1)
        src = match.group(2).strip()
        if src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        if src in ("placeholder", "{placeholder}"):
            return match.group(0)
        key = src.lstrip("/")
        if key.startswith("kb_images/") or key.startswith("kb/"):
            return f"![{alt}]({upload_asset_path(key)})"
        return match.group(0)

    return _IMAGE_REF_RE.sub(replace_image_ref, text)


def markdown_payload_for_storage_text(text: str) -> dict[str, Any]:
    """为 API 返回补充 Markdown 渲染字段（数据库存储的 text 本身多为 Markdown）。"""
    raw = text or ""
    fmt = "markdown" if is_markdown_content(raw) else "plain"
    payload: dict[str, Any] = {
        "textFormat": fmt,
        "hasImages": bool(extract_image_storage_keys(raw)),
    }
    if fmt == "markdown":
        payload["markdown"] = rewrite_markdown_asset_urls(raw)
    else:
        payload["markdown"] = raw
    return payload
