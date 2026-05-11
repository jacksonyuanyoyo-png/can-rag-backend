from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings


class FastGPTClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _base_url(self) -> str:
        return self._settings.FASTGPT_BASE_URL.rstrip("/")

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._settings.FASTGPT_API_KEY:
            h["Authorization"] = f"Bearer {self._settings.FASTGPT_API_KEY}"
        return h

    async def post_json(self, path: str, json: dict[str, Any]) -> httpx.Response:
        url = f"{self._base_url()}{path if path.startswith('/') else '/' + path}"
        return await self._client.post(url, headers=self._headers(), json=json)

    async def chat_completions(self, payload: dict[str, Any]) -> httpx.Response:
        p = self._settings.FASTGPT_CHAT_PATH
        path = p if p.startswith("/") else f"/{p}"
        return await self.post_json(path, payload)
