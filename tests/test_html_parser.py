from __future__ import annotations

from pathlib import Path

from app.services.rag.parsing.html_parser import HtmlDocumentParser

_FIXTURE = Path(__file__).parent / "fixtures" / "web" / "article.html"


def test_html_parser_extracts_markdown_blocks() -> None:
    document = HtmlDocumentParser().parse(_FIXTURE)
    assert document.full_text.strip()
    assert document.blocks
    assert any(block.heading for block in document.blocks) or len(document.blocks) >= 1


def test_html_parser_guess_source_url_from_header(tmp_path: Path) -> None:
    html_path = tmp_path / "saved.html"
    html_path.write_text(
        "Source URL: https://docs.vespa.ai/en/rag/rag.html\n\n"
        "<html><body><article><h1>Test</h1><p>Body text here.</p></article></body></html>",
        encoding="utf-8",
    )
    document = HtmlDocumentParser().parse(html_path)
    assert "Test" in document.full_text or "Body" in document.full_text
