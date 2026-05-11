from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings


class OpenAIClient:
    """封装 OpenAI Files API 与 Vector Stores API（相对 `OPENAI_BASE_URL`）。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _url(self, path: str) -> str:
        base = self._settings.OPENAI_BASE_URL.rstrip("/")
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{base}{suffix}"

    def _headers_json(self) -> dict[str, str]:
        key = self._settings.OPENAI_API_KEY.strip()
        if not key:
            raise ValueError("OPENAI_API_KEY 未配置")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def _headers(self) -> dict[str, str]:
        key = self._settings.OPENAI_API_KEY.strip()
        if not key:
            raise ValueError("OPENAI_API_KEY 未配置")
        return {"Authorization": f"Bearer {key}"}

    async def create_vector_store(self, body: dict[str, Any]) -> dict[str, Any]:
        r = await self._client.post(
            self._url("/vector_stores"),
            headers=self._headers_json(),
            json=body,
        )
        r.raise_for_status()
        return r.json()

    async def list_vector_stores(
        self,
        *,
        after: str | None = None,
        limit: int | None = None,
        order: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if after is not None:
            params["after"] = after
        if limit is not None:
            params["limit"] = limit
        if order is not None:
            params["order"] = order
        r = await self._client.get(
            self._url("/vector_stores"),
            headers=self._headers(),
            params=params or None,
        )
        r.raise_for_status()
        return r.json()

    async def retrieve_vector_store(self, vector_store_id: str) -> dict[str, Any]:
        r = await self._client.get(
            self._url(f"/vector_stores/{vector_store_id}"),
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def delete_vector_store(self, vector_store_id: str) -> dict[str, Any]:
        r = await self._client.delete(
            self._url(f"/vector_stores/{vector_store_id}"),
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def upload_file(self, *, filename: str, content: bytes, purpose: str) -> dict[str, Any]:
        files = {"file": (filename, content)}
        data = {"purpose": purpose}
        r = await self._client.post(
            self._url("/files"),
            headers=self._headers(),
            files=files,
            data=data,
        )
        r.raise_for_status()
        return r.json()

    async def list_files(
        self,
        *,
        purpose: str | None = None,
        after: str | None = None,
        limit: int | None = None,
        order: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if purpose is not None:
            params["purpose"] = purpose
        if after is not None:
            params["after"] = after
        if limit is not None:
            params["limit"] = limit
        if order is not None:
            params["order"] = order
        r = await self._client.get(
            self._url("/files"),
            headers=self._headers(),
            params=params or None,
        )
        r.raise_for_status()
        return r.json()

    async def attach_file_to_vector_store(
        self,
        vector_store_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        r = await self._client.post(
            self._url(f"/vector_stores/{vector_store_id}/files"),
            headers=self._headers_json(),
            json=body,
        )
        r.raise_for_status()
        return r.json()

    async def list_vector_store_files(
        self,
        vector_store_id: str,
        *,
        after: str | None = None,
        limit: int | None = None,
        order: str | None = None,
        filter_: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if after is not None:
            params["after"] = after
        if limit is not None:
            params["limit"] = limit
        if order is not None:
            params["order"] = order
        if filter_ is not None:
            params["filter"] = filter_
        r = await self._client.get(
            self._url(f"/vector_stores/{vector_store_id}/files"),
            headers=self._headers(),
            params=params or None,
        )
        r.raise_for_status()
        return r.json()
