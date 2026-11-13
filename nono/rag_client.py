"""Thin async client for the NONO RAG agent. Failures never break a conversation."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("nono.rag")


class RagClient:
    def __init__(self) -> None:
        headers = {"X-Nono-Key": settings.rag_api_key} if settings.rag_api_key else {}
        self._c = httpx.AsyncClient(base_url=settings.rag_url, headers=headers, timeout=httpx.Timeout(20, connect=3))
        self.online = False

    async def close(self) -> None:
        await self._c.aclose()

    async def _call(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = await self._c.request(method, path, **kw)
            r.raise_for_status()
            self.online = True
            return r.json() if "json" in r.headers.get("content-type", "") else r.text
        except httpx.HTTPError as exc:
            self.online = False
            log.warning("RAG %s %s failed: %s", method, path, exc)
            return None

    async def health(self) -> dict[str, Any] | None:
        return await self._call("GET", "/health")

    async def remember(self, text: str, **fields: Any) -> dict[str, Any] | None:
        return await self._call("POST", "/memories", json={"text": text, **{k: v for k, v in fields.items() if v is not None}})

    async def update(self, memory_id: str, **fields: Any) -> dict[str, Any] | None:
        return await self._call("PATCH", f"/memories/{memory_id}", json={k: v for k, v in fields.items() if v is not None})

    async def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        data = await self._call("POST", "/search", json={"query": query, "k": k, "min_score": 0.18})
        return (data or {}).get("results", [])

    async def proxy(self, method: str, path: str, params: dict[str, Any] | None = None, **kw: Any) -> httpx.Response:
        return await self._c.request(method, path, params=params, **kw)
