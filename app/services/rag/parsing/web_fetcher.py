from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; CAN-RAG/1.0; +https://www.fidelity.ca)"
)

# Google DNS JSON API（仅用于 SSRF 校验解析，不替代实际 HTTP 连接路径）
_GOOGLE_DNS_RESOLVE_URL = "https://dns.google/resolve"


class WebFetchError(ValueError):
    """网页抓取失败。"""


def validate_web_url(url: str, *, settings: Settings | None = None) -> str:
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
    cfg = settings or get_settings()
    _reject_private_host(
        host,
        use_public_dns=cfg.WEB_SSRF_USE_PUBLIC_DNS,
    )
    return normalized


def _reject_private_host(host: str, *, use_public_dns: bool) -> None:
    ips = _resolve_host_ips(host, use_public_dns=use_public_dns)
    if not ips:
        raise WebFetchError(f"无法解析主机名: {host}")
    for ip_str in ips:
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
            raise WebFetchError(
                f"不允许访问内网或保留地址（{host} → {ip_str}）。"
                "该 URL 在公共 DNS 下仍指向私网/保留段，无法通过 web-import 抓取。"
            )


def _resolve_host_ips(host: str, *, use_public_dns: bool) -> list[str]:
    if use_public_dns:
        public_ips = _resolve_via_google_dns(host)
        if public_ips:
            return public_ips
        logger.warning(
            "公共 DNS 解析失败，回退系统 DNS: host=%s",
            host,
        )
    return _resolve_via_system_dns(host)


def _resolve_via_google_dns(host: str) -> list[str]:
    """通过 DNS-over-HTTPS 查询，绕过本机 fake-ip 对 SSRF 校验的干扰。"""
    ips: list[str] = []
    try:
        with httpx.Client(timeout=httpx.Timeout(8.0)) as client:
            for record_type in (1, 28):  # A, AAAA
                response = client.get(
                    _GOOGLE_DNS_RESOLVE_URL,
                    params={"name": host, "type": str(record_type)},
                )
                response.raise_for_status()
                payload = response.json()
                ips.extend(_ips_from_dns_answer(payload))
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.debug("Google DNS 解析异常: host=%s reason=%s", host, exc)
        return []
    return _dedupe_preserve_order(ips)


def _ips_from_dns_answer(payload: dict[str, Any]) -> list[str]:
    ips: list[str] = []
    for item in payload.get("Answer") or []:
        if not isinstance(item, dict):
            continue
        data = str(item.get("data", "")).strip()
        if not data:
            continue
        try:
            ipaddress.ip_address(data)
        except ValueError:
            continue
        ips.append(data)
    return ips


def _resolve_via_system_dns(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebFetchError(f"无法解析主机名: {host}") from exc
    ips: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            ips.append(sockaddr[0])
    return _dedupe_preserve_order(ips)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def fetch_html(
    url: str,
    *,
    settings: Settings,
    user_agent: str | None = None,
) -> tuple[str, str]:
    """抓取 HTML，返回 (html, final_url)。"""
    validated = validate_web_url(url, settings=settings)
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
