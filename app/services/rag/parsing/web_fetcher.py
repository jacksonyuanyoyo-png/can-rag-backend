from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from app.core.config import Settings

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; CAN-RAG/1.0; +https://www.fidelity.ca)"
)


class WebFetchError(ValueError):
    """网页抓取失败。"""


def validate_web_url(url: str) -> str:
    normalized = (url or "").strip()
    if not normalized:
        raise WebFetchError("URL 不能为空")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise WebFetchError("仅支持 http/https URL")
    if not parsed.netloc:
        raise WebFetchError("URL 缺少主机名")
    host = parsed.hostname
    if host is None:
        raise WebFetchError("URL 主机名无效")
    lowered = host.lower()
    if lowered in {"localhost", "127.0.0.1", "::1"} or lowered.endswith(".local"):
        raise WebFetchError("不允许访问本地地址")
    _reject_private_host(host)
    return normalized


def _reject_private_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebFetchError(f"无法解析主机名: {host}") from exc
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise WebFetchError("不允许访问内网或保留地址")


def fetch_html(
    url: str,
    *,
    settings: Settings,
    user_agent: str | None = None,
) -> tuple[str, str]:
    """抓取 HTML，返回 (html, final_url)。"""
    validated = validate_web_url(url)
    headers = {
        "User-Agent": user_agent or settings.WEB_USER_AGENT or _DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en,en-US;q=0.9,fr;q=0.8",
    }
    timeout = httpx.Timeout(settings.WEB_FETCH_TIMEOUT_SECONDS)
    max_bytes = settings.WEB_FETCH_MAX_BYTES
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            with client.stream("GET", validated) as response:
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").lower()
                if "text/html" not in content_type and "application/xhtml" not in content_type:
                    if "text/" not in content_type and "application/json" not in content_type:
                        raise WebFetchError(
                            f"不支持的内容类型: {content_type or 'unknown'}"
                        )
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise WebFetchError(
                            f"响应体超过限制 ({max_bytes} 字节)"
                        )
                    chunks.append(chunk)
                raw = b"".join(chunks)
                final_url = str(response.url)
    except httpx.HTTPStatusError as exc:
        raise WebFetchError(
            f"HTTP {exc.response.status_code}: {validated}"
        ) from exc
    except httpx.RequestError as exc:
        raise WebFetchError(f"请求失败: {exc}") from exc

    encoding = "utf-8"
    try:
        html = raw.decode(encoding)
    except UnicodeDecodeError:
        html = raw.decode(encoding, errors="replace")
    if not html.strip():
        raise WebFetchError("页面内容为空")
    return html, final_url
