from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.rag.parsing.web_extractor import (
    extract_from_html,
    passes_quality_gate,
    url_to_base_filename,
)
from app.services.rag.parsing.web_fetcher import WebFetchError, validate_web_url
from app.services.rag.parsing.web_parser import parsed_document_from_extraction

_FIXTURE_HTML = Path(__file__).parent / "fixtures" / "web" / "article.html"


@pytest.fixture
def web_settings() -> Settings:
    return Settings(
        WEB_MIN_CONTENT_CHARS=80,
        WEB_LINK_DENSITY_MAX=0.5,
        WEB_ENABLE_BROWSER_FALLBACK=False,
    )


def test_validate_web_url_rejects_localhost() -> None:
    with pytest.raises(WebFetchError):
        validate_web_url("http://127.0.0.1/secret")


def test_validate_public_url_allows_vespa_docs_despite_fake_ip_system_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本机 DNS 可能把公网域名解析成 198.18.x（fake-ip）；校验应走公共 DNS。"""

    def _fake_system_dns(host: str) -> list[str]:
        return ["198.18.1.102"]

    monkeypatch.setattr(
        "app.services.rag.parsing.web_fetcher._resolve_via_system_dns",
        _fake_system_dns,
    )
    settings = Settings(WEB_SSRF_USE_PUBLIC_DNS=True)
    normalized = validate_web_url(
        "https://docs.vespa.ai/en/rag/rag.html",
        settings=settings,
    )
    assert normalized.startswith("https://docs.vespa.ai/")


def test_validate_rejects_true_private_ip_on_public_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_public_dns(host: str) -> list[str]:
        return ["10.0.0.1"]

    monkeypatch.setattr(
        "app.services.rag.parsing.web_fetcher._resolve_via_google_dns",
        _fake_public_dns,
    )
    with pytest.raises(WebFetchError, match="内网或保留地址"):
        validate_web_url(
            "https://internal.example.com/page",
            settings=Settings(WEB_SSRF_USE_PUBLIC_DNS=True),
        )


def test_extract_article_fixture_has_headings(web_settings: Settings) -> None:
    html = _FIXTURE_HTML.read_text(encoding="utf-8")
    result = extract_from_html(
        html,
        url="https://www.fidelity.ca/en/insights/articles/government-grants-resp/",
        settings=web_settings,
    )
    assert "CESG" in result.markdown
    assert "Your advisor" in result.markdown
    assert result.title is not None
    document = parsed_document_from_extraction(result)
    headings = [block.heading for block in document.blocks if block.heading]
    assert any("advisor" in (h or "").lower() for h in headings)


def test_passes_quality_gate_rejects_short_text(web_settings: Settings) -> None:
    assert not passes_quality_gate(
        "too short",
        min_chars=web_settings.WEB_MIN_CONTENT_CHARS,
        max_link_density=web_settings.WEB_LINK_DENSITY_MAX,
    )


def test_url_to_base_filename_prefers_title() -> None:
    name = url_to_base_filename(
        "https://www.fidelity.ca/en/insights/articles/government-grants-resp/",
        "Here's how to get government grants into your RESP",
    )
    assert name.endswith(".md")
    assert "government" in name.lower() or "RESP" in name
