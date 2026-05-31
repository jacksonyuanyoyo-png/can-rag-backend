from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import trafilatura
from readability import Document as ReadabilityDocument

from app.core.config import Settings
from app.services.rag.parsing.web_fetcher import WebFetchError, fetch_html
from app.services.rag.parsing.web_renderer import render_html_with_browser

logger = logging.getLogger(__name__)

_MARKDOWN_LINK_RE = re.compile(r"\]\([^)]+\)")


@dataclass(frozen=True, slots=True)
class WebExtractionResult:
    title: str | None
    markdown: str
    source_url: str
    method: str


def passes_quality_gate(
    text: str,
    *,
    min_chars: int,
    max_link_density: float,
) -> bool:
    stripped = text.strip()
    if len(stripped) < min_chars:
        return False
    link_chars = sum(len(match.group(0)) for match in _MARKDOWN_LINK_RE.finditer(stripped))
    if len(stripped) == 0:
        return False
    if link_chars / len(stripped) > max_link_density:
        return False
    lines = stripped.splitlines()
    if lines:
        from collections import Counter

        counts = Counter(line.strip() for line in lines if line.strip())
        if counts and counts.most_common(1)[0][1] > 5:
            return False
    return True


def _extract_with_trafilatura(html: str, *, url: str) -> tuple[str | None, str]:
    markdown = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=True,
        include_links=True,
        output_format="markdown",
        favor_precision=True,
    )
    metadata = trafilatura.extract_metadata(html, default_url=url)
    title = metadata.title.strip() if metadata and metadata.title else None
    body = (markdown or "").strip()
    return title, body


def _extract_with_readability(html: str, *, url: str) -> tuple[str | None, str]:
    doc = ReadabilityDocument(html, url=url)
    title = (doc.title() or "").strip() or None
    summary_html = doc.summary() or ""
    if not summary_html.strip():
        return title, ""
    body = trafilatura.html2txt(summary_html).strip()
    return title, body


def _compose_markdown(*, title: str | None, body: str) -> str:
    normalized_body = body.strip()
    if not normalized_body:
        return ""
    if title:
        heading = f"# {title.strip()}"
        if normalized_body.startswith("#"):
            return normalized_body
        return f"{heading}\n\n{normalized_body}"
    return normalized_body


def extract_from_html(
    html: str,
    *,
    url: str,
    settings: Settings,
) -> WebExtractionResult:
    title, body = _extract_with_trafilatura(html, url=url)
    method = "trafilatura"
    if not passes_quality_gate(
        body,
        min_chars=settings.WEB_MIN_CONTENT_CHARS,
        max_link_density=settings.WEB_LINK_DENSITY_MAX,
    ):
        alt_title, alt_body = _extract_with_readability(html, url=url)
        if len(alt_body) > len(body):
            title, body = alt_title, alt_body
            method = "readability"
    markdown = _compose_markdown(title=title, body=body)
    if not markdown.strip():
        raise WebFetchError("未能从页面抽取正文")
    return WebExtractionResult(
        title=title,
        markdown=markdown,
        source_url=url,
        method=method,
    )


def extract_from_url(
    url: str,
    *,
    settings: Settings,
    use_browser_fallback: bool | None = None,
) -> WebExtractionResult:
    validated = url.strip()
    html, final_url = fetch_html(validated, settings=settings)
    result = extract_from_html(html, url=final_url, settings=settings)
    if passes_quality_gate(
        result.markdown,
        min_chars=settings.WEB_MIN_CONTENT_CHARS,
        max_link_density=settings.WEB_LINK_DENSITY_MAX,
    ):
        return result

    browser_enabled = (
        use_browser_fallback
        if use_browser_fallback is not None
        else settings.WEB_ENABLE_BROWSER_FALLBACK
    )
    if not browser_enabled:
        return result

    rendered = render_html_with_browser(final_url, settings=settings)
    if not rendered:
        logger.info("浏览器渲染不可用或失败，使用静态 HTML 抽取结果: %s", final_url)
        return result

    browser_result = extract_from_html(rendered, url=final_url, settings=settings)
    if passes_quality_gate(
        browser_result.markdown,
        min_chars=settings.WEB_MIN_CONTENT_CHARS,
        max_link_density=settings.WEB_LINK_DENSITY_MAX,
    ):
        return WebExtractionResult(
            title=browser_result.title,
            markdown=browser_result.markdown,
            source_url=final_url,
            method=f"browser+{browser_result.method}",
        )
    if len(browser_result.markdown) > len(result.markdown):
        return WebExtractionResult(
            title=browser_result.title,
            markdown=browser_result.markdown,
            source_url=final_url,
            method=f"browser+{browser_result.method}",
        )
    return result


def url_to_base_filename(url: str, title: str | None) -> str:
    if title and title.strip():
        base = _sanitize_filename_stem(title.strip())
    else:
        path = urlparse(url).path.strip("/")
        base = _sanitize_filename_stem(path.replace("/", "-") if path else "web-page")
    if not base:
        base = "web-page"
    return f"{base}.md"


def _sanitize_filename_stem(name: str) -> str:
    cleaned = re.sub(r"[^\w\s\-.]", "", name, flags=re.UNICODE)
    cleaned = re.sub(r"[\s_]+", "-", cleaned.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned[:100] if cleaned else "web-page"
