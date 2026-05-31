from __future__ import annotations

import logging

from app.core.config import Settings

logger = logging.getLogger(__name__)


def render_html_with_browser(url: str, *, settings: Settings) -> str | None:
    """使用 Playwright 渲染页面；未安装 playwright 时返回 None。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.debug("playwright 未安装，跳过浏览器渲染")
        return None

    timeout_ms = int(settings.WEB_FETCH_TIMEOUT_SECONDS * 1000)
    user_agent = settings.WEB_USER_AGENT

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=user_agent)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(1500)
                html = page.content()
            finally:
                browser.close()
    except Exception:
        logger.exception("Playwright 渲染失败: %s", url)
        return None

    return html if html and html.strip() else None
