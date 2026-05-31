#!/usr/bin/env python3
"""本地诊断 Mini84 PDF 解析与增强。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from app.core.config import get_settings
from app.services.rag.parsing.pdf_parser import PdfDocumentParser
from app.services.rag.parsing.pdf_to_markdown import (
    is_sparse_pdf_text,
    parse_pdf_with_options,
    split_pdf_into_page_bytes,
)
from app.services.rag.vlm_service import VlmService

PDF = Path(
    "/Users/jackson/Documents/键盘说明书/Mini84 RGB三模布局图及功能说明2024.01.25.pdf"
)


def main() -> None:
    settings = get_settings()
    print("=== 配置 ===")
    print(f"VLM_ENABLED={settings.VLM_ENABLED}")
    print(f"VLM_MODEL={settings.VLM_MODEL}")
    print(f"OPENAI_API_KEY set={bool(settings.OPENAI_API_KEY.strip())}")
    print(f"PDF_AUTO_ENHANCE_MIN_CHARS={settings.PDF_AUTO_ENHANCE_MIN_CHARS}")

    print("\n=== pypdf 传统抽取 ===")
    legacy = PdfDocumentParser().parse(PDF)
    print(f"pages (blocks)={len(legacy.blocks)}")
    print(f"full_text len={len(legacy.full_text)}")
    print(f"sparse={is_sparse_pdf_text(legacy.full_text, min_chars=settings.PDF_AUTO_ENHANCE_MIN_CHARS)}")
    preview = legacy.full_text[:500].replace("\n", "\\n")
    print(f"preview: {preview!r}")

    pages = split_pdf_into_page_bytes(PDF.read_bytes())
    print(f"\n=== PDF 页数 (pypdf split)={len(pages)} ===")
    for i, pb in enumerate(pages[:3], start=1):
        print(f"  page {i} bytes={len(pb)}")

    print("\n=== 显式 pdfEnhancement=true ===")
    vlm = VlmService(settings)
    try:
        doc = parse_pdf_with_options(
            PDF,
            text_extraction=True,
            pdf_enhancement=True,
            settings=settings,
            vlm_service=vlm,
        )
        print(f"blocks={len(doc.blocks)}")
        print(f"full_text len={len(doc.full_text)}")
        print(f"preview:\n{doc.full_text[:1500]}")
        md_path = PDF.with_suffix(".md")
        if md_path.exists():
            print(f"\nmarkdown saved: {md_path} ({md_path.stat().st_size} bytes)")
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    main()
