"""MCP server tools, driven through FastMCP.call_tool with an injected local backend (hashing embedder,
rule extractor) — offline, zero setup. Verifies the tools, their schemas, and the markdown/json output."""
from __future__ import annotations

import json
import tempfile

import pytest

pytest.importorskip("mcp")

from engram.mcp import server as S  # noqa: E402
from engram.mcp.backends import LocalBackend, RemoteBackend  # noqa: E402
from engram.service import MemoryService  # noqa: E402


@pytest.fixture()
def local_backend():
    d = tempfile.mkdtemp(prefix="engram_mcp_")
    svc = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
    S.set_backend(LocalBackend(namespace="me", service=svc))
    yield
    S.set_backend(None)


def text_of(res) -> str:
    seq = res[0] if isinstance(res, tuple) else res  # newer mcp returns (content, structured)
    return seq[0].text


async def call(name: str, **args) -> str:
    return text_of(await S.mcp.call_tool(name, args))


@pytest.mark.asyncio
async def test_tools_are_registered_with_flat_schema():
    tools = {t.name: t for t in await S.mcp.list_tools()}
    assert {"engram_remember", "engram_recall", "engram_search", "engram_list_facts",
            "engram_profile", "engram_stats", "engram_add_fact", "engram_import",
            "engram_forget"} <= set(tools)
    # flat top-level params (not nested under "params")
    props = tools["engram_recall"].inputSchema.get("properties", {})
    assert "query" in props and "max_chunks" in props and "as_of" in props
    assert "redact_sensitive" in props
    search_props = tools["engram_search"].inputSchema.get("properties", {})
    assert "as_of" in search_props and "redact_sensitive" in search_props
    assert tools["engram_recall"].annotations.readOnlyHint is True
    assert tools["engram_stats"].annotations.readOnlyHint is True
    assert tools["engram_forget"].annotations.destructiveHint is True


@pytest.mark.asyncio
async def test_remember_then_recall_and_search(local_backend):
    out = await call("engram_remember", content="My name is Wei and I live in Shenzhen.")
    assert "Remembered" in out
    ctx = await call("engram_recall", query="Where does the user live?")
    assert "Shenzhen" in ctx
    js = await call("engram_recall", query="Where does the user live?", response_format="json")
    assert '"context"' in js
    ans = await call("engram_search", query="Where does the user live?")
    assert "Answer" in ans


@pytest.mark.asyncio
async def test_add_fact_list_and_profile(local_backend):
    await call("engram_add_fact", predicate="works_at", object="Moonshot AI")
    facts = await call("engram_list_facts")
    assert "Moonshot AI" in facts and "🔒" in facts  # user-asserted -> locked marker
    prof = await call("engram_profile")
    assert "User profile" in prof


@pytest.mark.asyncio
async def test_stats_tool_is_content_free(local_backend):
    await call("engram_remember", content="My private diagnosis is diabetes and I work at Acme.")
    await call("engram_add_fact", predicate="has_disease", object="diabetes")

    stats = await call("engram_stats")
    assert "Engram stats" in stats
    assert "Episodes:" in stats and "Consolidation backlog" in stats
    assert "Graph hygiene:" in stats
    assert "diabetes" not in stats.lower()
    assert "acme" not in stats.lower()

    raw = await call("engram_stats", response_format="json")
    data = json.loads(raw)
    assert data["counts"]["episodes"] >= 1
    assert "graph_orphan_entities" in data["counts"]
    assert "graph_stale_relations" in data["counts"]
    assert "time_range" in data
    assert "diabetes" not in raw.lower()


@pytest.mark.asyncio
async def test_import_tool(local_backend):
    arr = '[{"role":"user","content":"I have a cat named Pixel."}]'
    out = await call("engram_import", content=arr, format="messages")
    assert "Imported" in out and "episode" in out
    ctx = await call("engram_recall", query="What pet do I have?")
    assert "Pixel" in ctx


@pytest.mark.asyncio
async def test_recall_and_search_support_as_of(local_backend):
    records = json.dumps([
        {"session_id": "old", "content": "Wei works at Tencent.", "event_time": 1_700_000_000.0},
        {"session_id": "new", "content": "Wei works at Moonshot AI.", "event_time": 1_702_592_000.0},
    ])
    await call("engram_import", content=records, format="records")

    ctx = await call(
        "engram_recall",
        query="Where does Wei work?",
        max_chunks=0,
        as_of=1_700_864_000.0,
    )
    assert "Tencent" in ctx
    assert "Moonshot AI" not in ctx

    raw = await call(
        "engram_search",
        query="Where does Wei work?",
        as_of=1_700_864_000.0,
        response_format="json",
    )
    data = json.loads(raw)
    assert data["as_of"] == 1_700_864_000.0
    assert "Tencent" in data["answer"]
    assert all("Moonshot" not in fact for fact in data["facts"])


@pytest.mark.asyncio
async def test_recall_and_search_support_sensitive_redaction(local_backend):
    await call("engram_add_fact", predicate="has_disease", object="diabetes")
    await call("engram_add_fact", predicate="works_at", object="Acme")

    ctx = await call(
        "engram_recall",
        query="what do you know about me?",
        max_chunks=0,
        redact_sensitive=True,
    )
    assert "Acme" in ctx
    assert "diabetes" not in ctx.lower()

    raw = await call(
        "engram_search",
        query="what disease do I have?",
        redact_sensitive=True,
        response_format="json",
    )
    data = json.loads(raw)
    assert data["redacted_sensitive"] is True
    assert "diabetes" not in data["answer"].lower()
    assert all("diabetes" not in fact.lower() for fact in data["facts"])


@pytest.mark.asyncio
async def test_remote_backend_forwards_as_of():
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self):
            self.posts = []

        async def post(self, url, json=None, headers=None):
            self.posts.append((url, json, headers))
            return FakeResponse()

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    await backend.recall(
        "Where does Wei work?",
        lean=False,
        as_of=1_700_864_000.0,
        redact_sensitive=True,
    )

    url, body, headers = client.posts[-1]
    assert url == "http://engram.test/v1/recall"
    assert body["as_of"] == 1_700_864_000.0
    assert body["redact_sensitive"] is True
    assert headers["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_remote_backend_fetches_stats():
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"counts": {"episodes": 0}, "time_range": {}, "consolidation_backlog": False}

    class FakeClient:
        def __init__(self):
            self.gets = []

        async def get(self, url, headers=None):
            self.gets.append((url, headers))
            return FakeResponse()

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    data = await backend.stats()

    url, headers = client.gets[-1]
    assert url == "http://engram.test/v1/stats"
    assert headers["Authorization"] == "Bearer sk-test"
    assert data["counts"]["episodes"] == 0


@pytest.mark.asyncio
async def test_forget_requires_confirm(local_backend):
    await call("engram_remember", content="ephemeral note")
    guard = await call("engram_forget")  # no confirm
    assert "confirm=true" in guard
    done = await call("engram_forget", confirm=True)
    assert "erased" in done.lower()
    facts = await call("engram_list_facts")
    assert "No facts stored yet" in facts
