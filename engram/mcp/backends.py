"""Backends for the Engram MCP server.

Two ways to back the same tools, chosen at startup:

  * LocalBackend  — embeds `MemoryService` in-process. ONE fixed namespace per server (the agent's own
    memory). Zero external service: `python -m engram.mcp` just works (it persists to ~/.engram/data).
  * RemoteBackend — proxies a running Engram HTTP server (ENGRAM_API_URL + ENGRAM_API_KEY), so the same
    MCP tools drive your hosted/multi-tenant deployment; the api-key picks the namespace server-side.

Both return the identical dict shapes (the service / HTTP contract), so the tool layer formats one
schema regardless of mode.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Backend(Protocol):
    mode: str

    async def remember(self, content: str, session_id: str = "default") -> dict: ...
    async def recall(self, query: str, n_chunks: int = 6, lean: bool = True) -> dict: ...
    async def memories(self) -> dict: ...
    async def profile(self) -> dict: ...
    async def add_fact(self, subject: str, predicate: str, object: str) -> dict: ...
    async def import_(self, data: Any, format: str = "auto") -> dict: ...
    async def forget(self) -> dict: ...
    def describe(self) -> str: ...


class LocalBackend:
    """In-process memory via MemoryService, scoped to a single namespace. Synchronous service calls run
    in a worker thread so the embedder/LLM never block the MCP event loop."""

    mode = "local"

    def __init__(self, namespace: str = "me", service: Any = None) -> None:
        from ..service import MemoryService  # lazy: only when actually running local

        self.ns = namespace
        self.svc = service or MemoryService()

    async def remember(self, content: str, session_id: str = "default") -> dict:
        return await asyncio.to_thread(self.svc.remember, self.ns, content, session_id)

    async def recall(self, query: str, n_chunks: int = 6, lean: bool = True) -> dict:
        return await asyncio.to_thread(self.svc.recall, self.ns, query, lean, n_chunks)

    async def memories(self) -> dict:
        return await asyncio.to_thread(self.svc.memories, self.ns)

    async def profile(self) -> dict:
        return await asyncio.to_thread(self.svc.profile, self.ns)

    async def add_fact(self, subject: str, predicate: str, object: str) -> dict:
        return await asyncio.to_thread(self.svc.add_fact, self.ns, subject, predicate, object)

    async def import_(self, data: Any, format: str = "auto") -> dict:
        return await asyncio.to_thread(self.svc.import_, self.ns, None, format, data)

    async def forget(self) -> dict:
        return await asyncio.to_thread(self.svc.forget, self.ns)

    def describe(self) -> str:
        return f"local store at {self.svc.data_dir} (namespace '{self.ns}')"


class RemoteBackend:
    """Proxy a running Engram HTTP server. The api-key (Bearer) selects the namespace server-side."""

    mode = "remote"

    def __init__(self, api_url: str, key: str = "", client: Any = None) -> None:
        import httpx  # lazy: only when actually running remote

        self.api_url = api_url.rstrip("/")
        self.key = key
        self._client = client or httpx.AsyncClient(timeout=120.0)

    @property
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        return h

    async def _post(self, path: str, body: dict) -> dict:
        r = await self._client.post(self.api_url + path, json=body, headers=self._headers)
        r.raise_for_status()
        return r.json()

    async def _get(self, path: str) -> dict:
        r = await self._client.get(self.api_url + path, headers=self._headers)
        r.raise_for_status()
        return r.json()

    async def remember(self, content: str, session_id: str = "default") -> dict:
        return await self._post("/v1/remember", {"content": content, "session_id": session_id})

    async def recall(self, query: str, n_chunks: int = 6, lean: bool = True) -> dict:
        return await self._post("/v1/recall", {"query": query, "lean": lean, "n_chunks": n_chunks})

    async def memories(self) -> dict:
        return await self._get("/v1/memories")

    async def profile(self) -> dict:
        return await self._get("/v1/profile")

    async def add_fact(self, subject: str, predicate: str, object: str) -> dict:
        return await self._post("/v1/facts", {"subject": subject, "predicate": predicate, "object": object})

    async def import_(self, data: Any, format: str = "auto") -> dict:
        return await self._post("/v1/import", {"data": data, "format": format})

    async def forget(self) -> dict:
        return await self._post("/v1/forget", {})

    def describe(self) -> str:
        return f"remote Engram server at {self.api_url}"


def make_backend() -> Backend:
    """Pick the backend from the environment: ENGRAM_API_URL -> remote proxy, else local in-process
    memory in the ENGRAM_NAMESPACE namespace (default 'me')."""
    api_url = os.environ.get("ENGRAM_API_URL")
    if api_url:
        return RemoteBackend(api_url, os.environ.get("ENGRAM_API_KEY", ""))
    return LocalBackend(os.environ.get("ENGRAM_NAMESPACE", "me"))
