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
from urllib.parse import quote, urlencode


@runtime_checkable
class Backend(Protocol):
    mode: str

    async def remember(self, content: str, session_id: str = "default", scope: str = "auto") -> dict: ...
    async def recall(
        self,
        query: str,
        n_chunks: int = 6,
        lean: bool = True,
        session_id: str | None = None,
        as_of: float | None = None,
        redact_sensitive: bool = False,
    ) -> dict: ...
    async def close_session(
        self,
        session_id: str = "default",
        summarize: bool = True,
        clear_working: bool = True,
    ) -> dict: ...
    async def session_report(
        self,
        session_id: str,
        include_sensitive: bool = False,
    ) -> dict: ...
    async def sessions(
        self,
        limit: int = 20,
        offset: int = 0,
        q: str = "",
    ) -> dict: ...
    async def memories(self) -> dict: ...
    async def stats(self) -> dict: ...
    async def agent_status(self, session_id: str | None = None) -> dict: ...
    async def profile(self) -> dict: ...
    async def get_focus(self) -> dict: ...
    async def set_focus(
        self,
        track: list[str] | None = None,
        mute: list[str] | None = None,
    ) -> dict: ...
    async def add_fact(
        self,
        subject: str,
        predicate: str,
        object: str,
        sensitive: bool | None = None,
        category: str | None = None,
    ) -> dict: ...
    async def update_fact(
        self,
        fact_id: str,
        subject: str | None = None,
        predicate: str | None = None,
        object: str | None = None,
        sensitive: bool | None = None,
        category: str | None = None,
    ) -> dict | None: ...
    async def delete_fact(self, fact_id: str) -> dict: ...
    async def import_(self, data: Any, format: str = "auto") -> dict: ...
    async def export(self, include_sensitive: bool = False) -> dict: ...
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

    async def remember(self, content: str, session_id: str = "default", scope: str = "auto") -> dict:
        return await asyncio.to_thread(self.svc.remember, self.ns, content, session_id, scope)

    async def recall(
        self,
        query: str,
        n_chunks: int = 6,
        lean: bool = True,
        session_id: str | None = None,
        as_of: float | None = None,
        redact_sensitive: bool = False,
    ) -> dict:
        return await asyncio.to_thread(
            self.svc.recall,
            self.ns,
            query,
            lean=lean,
            n_chunks=n_chunks,
            session_id=session_id,
            as_of=as_of,
            redact_sensitive=redact_sensitive,
        )

    async def close_session(
        self,
        session_id: str = "default",
        summarize: bool = True,
        clear_working: bool = True,
    ) -> dict:
        return await asyncio.to_thread(
            self.svc.close_session,
            self.ns,
            session_id,
            summarize=summarize,
            clear_working=clear_working,
        )

    async def session_report(
        self,
        session_id: str,
        include_sensitive: bool = False,
    ) -> dict:
        return await asyncio.to_thread(
            self.svc.session_report,
            self.ns,
            session_id,
            include_sensitive,
        )

    async def sessions(self, limit: int = 20, offset: int = 0, q: str = "") -> dict:
        return await asyncio.to_thread(self.svc.sessions, self.ns, limit, offset, q)

    async def memories(self) -> dict:
        return await asyncio.to_thread(self.svc.memories, self.ns)

    async def stats(self) -> dict:
        return await asyncio.to_thread(self.svc.stats, self.ns)

    async def agent_status(self, session_id: str | None = None) -> dict:
        return await asyncio.to_thread(self.svc.agent_status, self.ns, session_id)

    async def profile(self) -> dict:
        return await asyncio.to_thread(self.svc.profile, self.ns)

    async def get_focus(self) -> dict:
        return await asyncio.to_thread(self.svc.get_focus, self.ns)

    async def set_focus(
        self,
        track: list[str] | None = None,
        mute: list[str] | None = None,
    ) -> dict:
        return await asyncio.to_thread(self.svc.set_focus, self.ns, track=track, mute=mute)

    async def add_fact(
        self,
        subject: str,
        predicate: str,
        object: str,
        sensitive: bool | None = None,
        category: str | None = None,
    ) -> dict:
        return await asyncio.to_thread(
            self.svc.add_fact,
            self.ns,
            subject,
            predicate,
            object,
            sensitive,
            category,
        )

    async def update_fact(
        self,
        fact_id: str,
        subject: str | None = None,
        predicate: str | None = None,
        object: str | None = None,
        sensitive: bool | None = None,
        category: str | None = None,
    ) -> dict | None:
        return await asyncio.to_thread(
            self.svc.update_fact,
            self.ns,
            fact_id,
            subject,
            predicate,
            object,
            sensitive,
            category,
        )

    async def delete_fact(self, fact_id: str) -> dict:
        return await asyncio.to_thread(self.svc.delete_fact, self.ns, fact_id)

    async def import_(self, data: Any, format: str = "auto") -> dict:
        return await asyncio.to_thread(self.svc.import_, self.ns, None, format, data)

    async def export(self, include_sensitive: bool = False) -> dict:
        return await asyncio.to_thread(self.svc.export, self.ns, include_sensitive)

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

    async def _patch(self, path: str, body: dict) -> dict:
        r = await self._client.patch(self.api_url + path, json=body, headers=self._headers)
        r.raise_for_status()
        return r.json()

    async def _put(self, path: str, body: dict) -> dict:
        r = await self._client.put(self.api_url + path, json=body, headers=self._headers)
        r.raise_for_status()
        return r.json()

    async def _delete(self, path: str) -> dict:
        r = await self._client.delete(self.api_url + path, headers=self._headers)
        r.raise_for_status()
        return r.json()

    async def _get(self, path: str) -> dict:
        r = await self._client.get(self.api_url + path, headers=self._headers)
        r.raise_for_status()
        return r.json()

    async def remember(self, content: str, session_id: str = "default", scope: str = "auto") -> dict:
        return await self._post("/v1/remember", {
            "content": content,
            "session_id": session_id,
            "scope": scope,
        })

    async def recall(
        self,
        query: str,
        n_chunks: int = 6,
        lean: bool = True,
        session_id: str | None = None,
        as_of: float | None = None,
        redact_sensitive: bool = False,
    ) -> dict:
        body = {"query": query, "lean": lean, "n_chunks": n_chunks}
        if session_id is not None:
            body["session_id"] = session_id
        if as_of is not None:
            body["as_of"] = as_of
        if redact_sensitive:
            body["redact_sensitive"] = True
        return await self._post("/v1/recall", body)

    async def close_session(
        self,
        session_id: str = "default",
        summarize: bool = True,
        clear_working: bool = True,
    ) -> dict:
        return await self._post("/v1/sessions/close", {
            "session_id": session_id,
            "summarize": summarize,
            "clear_working": clear_working,
        })

    async def session_report(
        self,
        session_id: str,
        include_sensitive: bool = False,
    ) -> dict:
        qs = urlencode({
            "session_id": session_id,
            "include_sensitive": "true" if include_sensitive else "false",
        })
        return await self._get("/v1/sessions/report?" + qs)

    async def sessions(self, limit: int = 20, offset: int = 0, q: str = "") -> dict:
        qs = urlencode({"limit": limit, "offset": offset, "q": q})
        return await self._get("/v1/sessions?" + qs)

    async def memories(self) -> dict:
        return await self._get("/v1/memories")

    async def stats(self) -> dict:
        return await self._get("/v1/stats")

    async def agent_status(self, session_id: str | None = None) -> dict:
        query = f"?{urlencode({'session_id': session_id})}" if session_id is not None else ""
        return await self._get("/v1/agent/status" + query)

    async def profile(self) -> dict:
        return await self._get("/v1/profile")

    async def get_focus(self) -> dict:
        return await self._get("/v1/focus")

    async def set_focus(
        self,
        track: list[str] | None = None,
        mute: list[str] | None = None,
    ) -> dict:
        body: dict[str, Any] = {}
        if track is not None:
            body["track"] = track
        if mute is not None:
            body["mute"] = mute
        return await self._put("/v1/focus", body)

    async def add_fact(
        self,
        subject: str,
        predicate: str,
        object: str,
        sensitive: bool | None = None,
        category: str | None = None,
    ) -> dict:
        body = {"subject": subject, "predicate": predicate, "object": object}
        if sensitive is not None:
            body["sensitive"] = sensitive
        if category is not None:
            body["category"] = category
        return await self._post("/v1/facts", body)

    async def update_fact(
        self,
        fact_id: str,
        subject: str | None = None,
        predicate: str | None = None,
        object: str | None = None,
        sensitive: bool | None = None,
        category: str | None = None,
    ) -> dict | None:
        body: dict[str, Any] = {}
        if subject is not None:
            body["subject"] = subject
        if predicate is not None:
            body["predicate"] = predicate
        if object is not None:
            body["object"] = object
        if sensitive is not None:
            body["sensitive"] = sensitive
        if category is not None:
            body["category"] = category
        return await self._patch(f"/v1/facts/{quote(fact_id, safe='')}", body)

    async def delete_fact(self, fact_id: str) -> dict:
        return await self._delete(f"/v1/facts/{quote(fact_id, safe='')}")

    async def import_(self, data: Any, format: str = "auto") -> dict:
        return await self._post("/v1/import", {"data": data, "format": format})

    async def export(self, include_sensitive: bool = False) -> dict:
        qs = urlencode({"include_sensitive": "true" if include_sensitive else "false"})
        return await self._get("/v1/export?" + qs)

    async def forget(self) -> dict:
        return await self._post("/v1/forget", {"confirm": True})

    def describe(self) -> str:
        return f"remote Engram server at {self.api_url}"


def make_backend() -> Backend:
    """Pick the backend from the environment: ENGRAM_API_URL -> remote proxy, else local in-process
    memory in the ENGRAM_NAMESPACE namespace (default 'me')."""
    api_url = os.environ.get("ENGRAM_API_URL")
    if api_url:
        return RemoteBackend(api_url, os.environ.get("ENGRAM_API_KEY", ""))
    return LocalBackend(os.environ.get("ENGRAM_NAMESPACE", "me"))
