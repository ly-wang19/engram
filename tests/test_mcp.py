"""MCP server tools, driven through FastMCP.call_tool with an injected local backend (hashing embedder,
rule extractor) — offline, zero setup. Verifies the tools, their schemas, and the markdown/json output."""
from __future__ import annotations

import tempfile

import pytest

pytest.importorskip("mcp")

from engram.mcp import server as S  # noqa: E402
from engram.mcp.backends import LocalBackend  # noqa: E402
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
            "engram_profile", "engram_add_fact", "engram_import", "engram_forget"} <= set(tools)
    # flat top-level params (not nested under "params")
    props = tools["engram_recall"].inputSchema.get("properties", {})
    assert "query" in props and "max_chunks" in props
    assert tools["engram_recall"].annotations.readOnlyHint is True
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
async def test_import_tool(local_backend):
    arr = '[{"role":"user","content":"I have a cat named Pixel."}]'
    out = await call("engram_import", content=arr, format="messages")
    assert "Imported" in out and "episode" in out
    ctx = await call("engram_recall", query="What pet do I have?")
    assert "Pixel" in ctx


@pytest.mark.asyncio
async def test_forget_requires_confirm(local_backend):
    await call("engram_remember", content="ephemeral note")
    guard = await call("engram_forget")  # no confirm
    assert "confirm=true" in guard
    done = await call("engram_forget", confirm=True)
    assert "erased" in done.lower()
    facts = await call("engram_list_facts")
    assert "No facts stored yet" in facts
