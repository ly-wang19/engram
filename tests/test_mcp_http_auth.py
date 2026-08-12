"""The MCP streamable-HTTP transport must not be an unauthenticated door into memory.

`python -m engram.mcp --http` is loopback-only by default; exposing it on a non-loopback host requires
a Bearer token (fail-closed, same philosophy as ENGRAM_API_KEYS/ENGRAM_OPEN on the REST server).
These tests exercise the pure ASGI gate and the fail-closed launch policy without starting a server.
"""
from __future__ import annotations

import asyncio

import pytest

from engram.mcp.__main__ import _BearerGate, _require_http_token


class _Inner:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run(gate, scope):
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    asyncio.run(gate(scope, receive, send))
    return sent


def _http_scope(auth: str | None) -> dict:
    headers = [(b"host", b"example.com")]
    if auth is not None:
        headers.append((b"authorization", auth.encode("latin-1")))
    return {"type": "http", "headers": headers}


def test_gate_rejects_missing_token():
    inner = _Inner()
    sent = _run(_BearerGate(inner, "secret"), _http_scope(None))
    assert sent[0]["status"] == 401
    assert not inner.called


def test_gate_rejects_wrong_token():
    inner = _Inner()
    sent = _run(_BearerGate(inner, "secret"), _http_scope("Bearer nope"))
    assert sent[0]["status"] == 401
    assert not inner.called


def test_gate_passes_valid_token():
    inner = _Inner()
    sent = _run(_BearerGate(inner, "secret"), _http_scope("Bearer secret"))
    assert inner.called
    assert sent[0]["status"] == 200


def test_gate_ignores_non_http_scopes():
    inner = _Inner()
    _run(_BearerGate(inner, "secret"), {"type": "lifespan"})
    assert inner.called  # lifespan/websocket handshake pass through to the app


def test_loopback_without_token_is_allowed():
    _require_http_token("127.0.0.1", "", allow_open=False)
    _require_http_token("localhost", "", allow_open=False)
    _require_http_token("::1", "", allow_open=False)


def test_non_loopback_without_token_fails_closed():
    with pytest.raises(SystemExit):
        _require_http_token("0.0.0.0", "", allow_open=False)


def test_non_loopback_with_token_or_explicit_open_is_allowed():
    _require_http_token("0.0.0.0", "some-token", allow_open=False)
    _require_http_token("0.0.0.0", "", allow_open=True)  # explicit operator override
