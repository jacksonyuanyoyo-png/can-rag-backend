#!/usr/bin/env python3
"""对 Mini84 说明书 PDF 做 PDF 增强解析联调（需 .env 中 OPENAI_API_KEY）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.domain.import_job import ChunkingConfig
from app.services.rag.chunking_service import ChunkingService
from app.services.rag.parsing.image_store import ImageStore
from app.services.rag.parsing.pdf_to_markdown import (
    enhanced_markdown_path_for,
    is_legacy_pdf_noise,
    is_usable_vlm_markdown,
    parse_pdf_with_options,
)
from app.services.rag.vlm_service import VlmService

DEFAULT_PDF = Path(
    "/Users/jackson/Documents/键盘说明书/Mini84 RGB三模布局图及功能说明2024.01.25.pdf"
)

_REFUSAL_SNIPPETS = (
    "抱歉",
    "无法查看",
    "没有文字或文档",
    "USB设备的照片",
    "1212 BCLCLM",
)


def main() -> int:
    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    if not pdf_path.is_file():
        print(f"文件不存在: {pdf_path}")
        return 1

    settings = get_settings()
    if not settings.OPENAI_API_KEY.strip():
        print("缺少 OPENAI_API_KEY，请在 .env 中配置")
        return 1

    print(f"PDF: {pdf_path}")
    print(f"PDF_VLM_MODEL={settings.PDF_VLM_MODEL} DPI={settings.PDF_RENDER_DPI}")

    vlm = VlmService(settings)
    upload_root = settings.upload_root_resolved
    document = parse_pdf_with_options(
        pdf_path,
        text_extraction=True,
        pdf_enhancement=True,
        settings=settings,
        vlm_service=vlm,
        image_store=ImageStore(upload_root),
    )

    full = document.full_text or ""
    print(f"\n--- 解析结果 ---")
    print(f"full_text 长度: {len(full)}")
    print(f"blocks: {len(document.blocks)} images: {len(document.images)}")

    for bad in _REFUSAL_SNIPPETS:
        if bad in full:
            print(f"FAIL: 正文含拒答/垃圾片段: {bad!r}")
            return 1

    if is_legacy_pdf_noise(full):
        print("FAIL: 正文仍为 pypdf 垃圾文本")
        return 1

    if not is_usable_vlm_markdown(full, min_chars=settings.PDF_VLM_MIN_MARKDOWN_CHARS):
        print("FAIL: 正文未通过可用性校验")
        return 1

    md_path = enhanced_markdown_path_for(pdf_path)
    if md_path.exists():
        print(f"已保存增强 Markdown: {md_path}")

    config = ChunkingConfig(strategy="default", meta_headings=True)
    chunks = ChunkingService(settings).split(document, config)
    print(f"data chunks: {len(chunks)}")
    for i, chunk in enumerate(chunks[:8], start=1):
        preview = chunk.text.replace("\n", " ")[:100]
        print(f"  #{i} page={chunk.page} len={len(chunk.text)} {preview!r}")

    if len(chunks) < 2:
        print("WARN: 切片过少，请检查分页 Markdown")

    print("\nOK: PDF 增强解析通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
