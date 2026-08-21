"""EngramClient — the Python SDK for the Engram memory server.

The agent ecosystem is Python-first, so this ships inside the core package rather than as a separate
install: `pip install engram-memory`, then `from engram.client import EngramClient`. It is the peer of
the TypeScript SDK in clients/typescript and deliberately mirrors its method names, so the two read the
same way and neither drifts into being the "real" one.

Zero runtime dependencies — it speaks to the server over stdlib urllib, keeping the charter's promise
that installing Engram pulls in nothing. Like the TS client's injectable `fetch`, `transport` swaps the
HTTP layer for httpx, requests, or an in-process test client without touching call sites.

    from engram.client import EngramClient

    engram = EngramClient(base_url="http://localhost:8000", api_key="sk-engram-...")
    engram.remember("I live in Shenzhen and work on retrieval.")
    print(engram.recall("where do I live?")["context"])

One Bearer key is one isolated namespace on the server, so a client instance is a tenant.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

__all__ = ["EngramClient", "EngramError", "Transport"]

# One request in, (status, response headers, body) out. Never raises for HTTP status -- the client maps
# status to errors, so a custom transport only has to move bytes. Headers are part of the contract
# because some of the server's answer lives there: a 429 carries Retry-After, and an error object that
# cannot report how long to wait is an error object nobody can act on.
Transport = Callable[[str, str, dict, Optional[bytes], float], "tuple[int, dict, bytes]"]


class EngramError(Exception):
    """Any non-2xx response, or a connection failure (status 0).

    `status` lets a caller branch without parsing prose: 401 means the key is wrong, 429 means back off
    (`retry_after` carries the server's Retry-After), 503 means the server is misconfigured rather than
    the request being bad.
    """

    def __init__(self, status: int, message: str, detail: Any = None, retry_after: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.retry_after = retry_after


def _safe_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _urllib_transport(
    method: str, url: str, headers: dict, body: Optional[bytes], timeout: float
) -> "tuple[int, dict, bytes]":
    request = urllib.request.Request(url, data=body, method=method)
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - caller's URL
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:  # a non-2xx response still carries a readable body
        return exc.code, dict(exc.headers or {}), exc.read()
    except urllib.error.URLError as exc:  # never reached the server: DNS, refused, TLS
        raise EngramError(0, f"cannot reach Engram at {url}: {exc.reason}") from exc


class EngramClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        transport: Optional[Transport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._transport: Transport = transport or _urllib_transport

    # --- plumbing ---

    def _request(
        self,
        method: str,
        path: str,
        body: Any = None,
        params: Optional[dict] = None,
        extra_headers: Optional[dict] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"

        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)

        status, response_headers, raw = self._transport(method, url, headers, data, self.timeout)
        if status < 200 or status >= 300:
            raise self._error(status, response_headers, raw)
        return _safe_json(raw)

    @staticmethod
    def _error(status: int, headers: dict, raw: bytes) -> EngramError:
        detail = _safe_json(raw)
        message = f"Engram request failed ({status})"
        if isinstance(detail, dict) and detail.get("detail"):
            reported = detail["detail"]
            message = reported if isinstance(reported, str) else json.dumps(reported, ensure_ascii=False)
        # Header names are case-insensitive on the wire; normalise rather than trust one spelling.
        lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
        try:
            retry_after = int(lowered["retry-after"])
        except (KeyError, TypeError, ValueError):
            retry_after = None
        return EngramError(status, message, detail, retry_after)

    @staticmethod
    def _idempotency(key: Optional[str]) -> Optional[dict]:
        return {"Idempotency-Key": key} if key else None

    # --- service ---

    def health(self) -> dict:
        return self._request("GET", "/health")

    def ready(self) -> dict:
        return self._request("GET", "/ready")

    def metrics(self) -> dict:
        """Aggregate latency, volume and token counters. Unauthenticated on the server."""
        return self._request("GET", "/metrics")

    # --- writing ---

    def remember(
        self,
        content: str,
        session_id: str = "default",
        scope: str = "auto",
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Store a message. Pass `idempotency_key` so a retry after a timeout does not store it twice."""
        return self._request(
            "POST",
            "/v1/remember",
            {"content": content, "session_id": session_id, "scope": scope},
            extra_headers=self._idempotency(idempotency_key),
        )

    def import_(
        self,
        sessions: Optional[list] = None,
        data: Any = None,
        format: str = "auto",
        consolidate: bool = True,
        summarize: bool = True,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Bulk-ingest a history. The most expensive call there is, so it is the one most worth an
        idempotency key."""
        return self._request(
            "POST",
            "/v1/import",
            {
                "sessions": sessions,
                "data": data,
                "format": format,
                "consolidate": consolidate,
                "summarize": summarize,
            },
            extra_headers=self._idempotency(idempotency_key),
        )

    def export(self, include_sensitive: bool = False) -> dict:
        return self._request("GET", "/v1/export", params={"include_sensitive": include_sensitive})

    def forget(self, confirm: bool = False) -> dict:
        """Erase this namespace. Requires `confirm=True`; the server refuses otherwise."""
        return self._request("POST", "/v1/forget", {}, params={"confirm": confirm})

    # --- reading ---

    def recall(
        self,
        query: str,
        lean: bool = True,
        n_chunks: int = 6,
        session_id: Optional[str] = None,
        as_of: Optional[float] = None,
        redact_sensitive: bool = False,
        answer: bool = False,
    ) -> dict:
        return self._request(
            "POST",
            "/v1/recall",
            {
                "query": query,
                "lean": lean,
                "n_chunks": n_chunks,
                "session_id": session_id,
                "as_of": as_of,
                "redact_sensitive": redact_sensitive,
                "answer": answer,
            },
        )

    def search(self, query: str, **kwargs: Any) -> dict:
        """A direct factual answer rather than a context to answer from."""
        return self.recall(query, lean=False, **kwargs)

    def memories(self, limit: Optional[int] = None, offset: int = 0, **params: Any) -> dict:
        return self._request("GET", "/v1/memories", params={"limit": limit, "offset": offset, **params})

    def profile(self, structured: bool = False) -> dict:
        return self._request("GET", "/v1/profile/structured" if structured else "/v1/profile")

    def stats(self) -> dict:
        return self._request("GET", "/v1/stats")

    def graph(self, **params: Any) -> dict:
        return self._request("GET", "/v1/graph", params=params)

    def agent_status(self, session_id: Optional[str] = None) -> dict:
        return self._request("GET", "/v1/agent/status", params={"session_id": session_id})

    # --- sessions ---

    def sessions(self, **params: Any) -> dict:
        return self._request("GET", "/v1/sessions", params=params)

    def session_report(self, session_id: str, **params: Any) -> dict:
        return self._request("GET", "/v1/sessions/report", params={"session_id": session_id, **params})

    def close_session(self, session_id: str = "default", summarize: bool = True,
                      clear_working: bool = True) -> dict:
        return self._request(
            "POST",
            "/v1/sessions/close",
            {"session_id": session_id, "summarize": summarize, "clear_working": clear_working},
        )

    # --- facts ---

    def add_fact(self, subject: str, predicate: str, object: str, **fields: Any) -> dict:
        return self._request(
            "POST", "/v1/facts", {"subject": subject, "predicate": predicate, "object": object, **fields}
        )

    def delete_fact(self, fact_id: str) -> dict:
        return self._request("DELETE", f"/v1/facts/{urllib.parse.quote(fact_id, safe='')}")

    def conflicts(self) -> dict:
        return self._request("GET", "/v1/conflicts")

    def resolve_conflict(self, conflict_id: str, keep: str = "newer") -> dict:
        path = f"/v1/conflicts/{urllib.parse.quote(conflict_id, safe='')}/resolve"
        return self._request("POST", path, {"keep": keep})

    # --- working memory, focus, policy ---

    def add_working(self, content: str, session_id: str = "default", kind: str = "state",
                    **fields: Any) -> dict:
        return self._request(
            "POST", "/v1/working",
            {"content": content, "session_id": session_id, "kind": kind, **fields},
        )

    def working_memory(self, session_id: Optional[str] = None) -> dict:
        return self._request("GET", "/v1/working", params={"session_id": session_id})

    def clear_working(self, session_id: str) -> dict:
        return self._request("DELETE", "/v1/working", params={"session_id": session_id})

    def get_focus(self) -> dict:
        return self._request("GET", "/v1/focus")

    def set_focus(self, track: Optional[list] = None, mute: Optional[list] = None) -> dict:
        return self._request("PUT", "/v1/focus", {"track": track, "mute": mute})

    def get_policy(self) -> dict:
        return self._request("GET", "/v1/policy")

    def set_policy(self, **fields: Any) -> dict:
        return self._request("PUT", "/v1/policy", fields)

    # --- admin ---
    #
    # These need ENGRAM_ADMIN_TOKEN as the api_key, not a tenant key -- a separate client instance,
    # because mixing an admin token into a tenant client would send it on every ordinary call.

    def issue_key(self, user: str, label: str = "") -> dict:
        """Mint a tenant key. The plaintext is in the response and nowhere else."""
        return self._request("POST", "/v1/admin/keys", {"user": user, "label": label})

    def list_keys(self, user: Optional[str] = None) -> dict:
        return self._request("GET", "/v1/admin/keys", params={"user": user})

    def revoke_key(self, key_id: str) -> dict:
        return self._request("DELETE", f"/v1/admin/keys/{urllib.parse.quote(key_id, safe='')}")
