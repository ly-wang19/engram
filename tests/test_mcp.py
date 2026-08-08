"""MCP server tools, driven through FastMCP.call_tool with an injected local backend (hashing embedder,
rule extractor) — offline, zero setup. Verifies the tools, their schemas, and the markdown/json output."""
from __future__ import annotations

import json
import re
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
    yield svc
    S.set_backend(None)


def text_of(res) -> str:
    seq = res[0] if isinstance(res, tuple) else res  # newer mcp returns (content, structured)
    return seq[0].text


async def call(name: str, **args) -> str:
    return text_of(await S.mcp.call_tool(name, args))


@pytest.mark.asyncio
async def test_tools_are_registered_with_flat_schema():
    tools = {t.name: t for t in await S.mcp.list_tools()}
    assert {"engram_remember", "engram_recall", "engram_close_session", "engram_erase_session",
            "engram_session_report",
            "engram_list_sessions", "engram_search",
            "engram_list_facts", "engram_profile", "engram_agent_status", "engram_stats",
            "engram_add_fact", "engram_update_fact", "engram_delete_fact", "engram_get_focus",
            "engram_set_focus", "engram_get_twin_contract", "engram_list_capabilities",
            "engram_authorize_twin_action", "engram_record_twin_action",
            "engram_import", "engram_export", "engram_forget"} <= set(tools)
    # flat top-level params (not nested under "params")
    props = tools["engram_recall"].inputSchema.get("properties", {})
    assert {
        "query", "max_chunks", "session_id", "as_of", "known_at"
    } <= set(props)
    assert "redact_sensitive" in props
    remember_props = tools["engram_remember"].inputSchema.get("properties", {})
    assert "content" in remember_props and "session_id" in remember_props and "scope" in remember_props
    add_props = tools["engram_add_fact"].inputSchema.get("properties", {})
    assert {"subject", "predicate", "object", "sensitive", "category"} <= set(add_props)
    update_props = tools["engram_update_fact"].inputSchema.get("properties", {})
    assert {"fact_id", "subject", "predicate", "object", "sensitive", "category"} <= set(update_props)
    focus_props = tools["engram_set_focus"].inputSchema.get("properties", {})
    assert {"track", "mute"} <= set(focus_props)
    status_props = tools["engram_agent_status"].inputSchema.get("properties", {})
    assert "session_id" in status_props
    report_props = tools["engram_session_report"].inputSchema.get("properties", {})
    assert {"session_id", "include_sensitive"} <= set(report_props)
    erase_session_props = tools["engram_erase_session"].inputSchema.get("properties", {})
    assert {"session_id", "confirm"} <= set(erase_session_props)
    sessions_props = tools["engram_list_sessions"].inputSchema.get("properties", {})
    assert {"limit", "offset", "q"} <= set(sessions_props)
    search_props = tools["engram_search"].inputSchema.get("properties", {})
    assert {"as_of", "known_at", "redact_sensitive"} <= set(search_props)
    authorize_props = tools["engram_authorize_twin_action"].inputSchema.get("properties", {})
    assert {"capability", "permission", "resource", "high_risk", "external_write"} <= set(
        authorize_props
    )
    assert "human_confirmed" not in authorize_props
    record_props = tools["engram_record_twin_action"].inputSchema.get("properties", {})
    assert {"decision_id", "outcome", "provenance"} <= set(record_props)
    assert "executed_at" not in record_props
    assert tools["engram_recall"].annotations.readOnlyHint is True
    assert tools["engram_get_focus"].annotations.readOnlyHint is True
    assert tools["engram_set_focus"].annotations.readOnlyHint is False
    assert tools["engram_stats"].annotations.readOnlyHint is True
    assert tools["engram_agent_status"].annotations.readOnlyHint is True
    assert tools["engram_session_report"].annotations.readOnlyHint is True
    assert tools["engram_list_sessions"].annotations.readOnlyHint is True
    assert tools["engram_get_twin_contract"].annotations.readOnlyHint is True
    assert tools["engram_list_capabilities"].annotations.readOnlyHint is True
    assert tools["engram_authorize_twin_action"].annotations.readOnlyHint is False
    assert tools["engram_record_twin_action"].annotations.readOnlyHint is False
    assert tools["engram_export"].annotations.readOnlyHint is True
    assert tools["engram_close_session"].annotations.readOnlyHint is False
    assert tools["engram_erase_session"].annotations.destructiveHint is True
    assert tools["engram_forget"].annotations.destructiveHint is True


@pytest.mark.asyncio
async def test_twin_tools_default_deny_hide_credentials_and_never_self_confirm(local_backend):
    local_backend.revise_twin_contract(
        "me",
        {
            "goals": [{"title": "Protect focus"}],
            "boundaries": [
                {
                    "description": "Never change the private calendar without approval",
                    "effect": "require_confirmation",
                    "capability": "calendar",
                    "scopes": ["calendars/private/**"],
                    "minimum_permission": "execute",
                }
            ],
        },
    )
    contract = await call("engram_get_twin_contract", response_format="json")
    assert "Protect focus" in contract
    assert "calendars/private/**" not in contract
    assert '"effect"' not in contract

    denied = json.loads(await call(
        "engram_authorize_twin_action",
        capability="calendar",
        permission="execute",
        resource="calendars/private/events/42",
        response_format="json",
    ))
    assert denied["decision"]["status"] == "denied"
    assert denied["executed"] is False

    local_backend.grant_capability(
        "me",
        capability="calendar",
        permission="execute",
        scopes=["calendars/private/**"],
        credential_ref={"provider": "vault", "key": "calendar/private"},
    )
    listed = await call("engram_list_capabilities", response_format="json")
    assert '"credential_configured": true' in listed
    assert "credential_ref" not in listed
    assert "calendar/private" not in listed
    assert '"provider"' not in listed

    pending = json.loads(await call(
        "engram_authorize_twin_action",
        capability="calendar",
        permission="execute",
        resource="calendars/private/events/42",
        external_write=True,
        response_format="json",
    ))
    assert pending["decision"]["status"] == "requires_confirmation"
    assert pending["executed"] is False
    rejected_record = await call(
        "engram_record_twin_action",
        decision_id=pending["decision"]["id"],
        outcome="must not be recorded as executed",
    )
    assert "Could not record" in rejected_record


@pytest.mark.asyncio
async def test_twin_tool_records_only_an_existing_allowed_decision(local_backend):
    local_backend.grant_capability(
        "me",
        capability="notes",
        permission="draft",
        scopes=["notes/personal/**"],
    )
    allowed = json.loads(await call(
        "engram_authorize_twin_action",
        capability="notes",
        permission="draft",
        resource="notes/personal/weekly-plan",
        response_format="json",
    ))
    assert allowed["decision"]["status"] == "allowed"
    assert allowed["executed"] is False

    recorded = json.loads(await call(
        "engram_record_twin_action",
        decision_id=allowed["decision"]["id"],
        outcome="Trusted executor prepared a reversible draft",
        provenance=["executor:notes-1"],
        response_format="json",
    ))
    assert recorded["ok"] is True
    assert recorded["action"]["executed_at"] > allowed["decision"]["decided_at"]

    missing = await call(
        "engram_record_twin_action",
        decision_id="decision_missing",
        outcome="should not be accepted",
    )
    assert "Could not record" in missing


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
async def test_recall_can_include_session_working_memory(local_backend):
    local_backend.add_working("me", "today I am checking release notes", session_id="agent-thread-1")

    ctx = await call(
        "engram_recall",
        query="what am I doing right now?",
        max_chunks=0,
        session_id="agent-thread-1",
    )

    assert "release notes" in ctx


@pytest.mark.asyncio
async def test_remember_scope_working_keeps_state_out_of_durable_facts(local_backend):
    out = await call(
        "engram_remember",
        content="today I am checking release notes",
        session_id="agent-thread-1",
        scope="working",
    )

    assert "working memory" in out
    assert local_backend.stats("me")["counts"]["working_live"] == 1
    assert local_backend.stats("me")["counts"]["facts_live"] == 0
    ctx = await call(
        "engram_recall",
        query="what am I doing right now?",
        max_chunks=0,
        session_id="agent-thread-1",
    )
    assert "release notes" in ctx


@pytest.mark.asyncio
async def test_close_session_tool(local_backend):
    await call("engram_remember", content="I work at Acme.", session_id="s1")
    out = await call("engram_close_session", session_id="s1")
    assert "Closed session `s1`" in out
    assert "episode" in out

    raw = await call("engram_close_session", session_id="s1", response_format="json")
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["session_id"] == "s1"


@pytest.mark.asyncio
async def test_session_report_tool_audits_saved_facts(local_backend):
    session = "codex:super-memory:thread"
    await call(
        "engram_remember",
        content="Project decision: the launch checklist must include committed eval logs.",
        session_id=session,
    )
    await call("engram_close_session", session_id=session)

    report = await call("engram_session_report", session_id=session)

    assert "Engram session report" in report
    assert f"Session: `{session}`" in report
    assert "committed eval logs" in report

    raw = await call("engram_session_report", session_id=session, response_format="json")
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["session_id"] == session
    assert data["facts_added"] >= 1


@pytest.mark.asyncio
async def test_add_fact_list_and_profile(local_backend):
    await call("engram_add_fact", predicate="works_at", object="Moonshot AI")
    facts = await call("engram_list_facts")
    assert "Moonshot AI" in facts and "🔒" in facts  # user-asserted -> locked marker
    assert re.search(r"`ft_[^`]+`", facts)
    prof = await call("engram_profile")
    assert "User profile" in prof


@pytest.mark.asyncio
async def test_update_and_delete_fact_tools(local_backend):
    await call("engram_add_fact", predicate="works_at", object="ByteDance")
    raw = await call("engram_list_facts", response_format="json")
    fid = json.loads(raw)["facts"][0]["id"]

    updated = await call("engram_update_fact", fact_id=fid, object="Moonshot AI", sensitive=False)
    assert f"Updated fact `{fid}`" in updated
    facts = await call("engram_list_facts")
    assert "Moonshot AI" in facts
    assert "ByteDance" not in facts

    guard = await call("engram_delete_fact", fact_id=fid)
    assert "confirm=true" in guard
    deleted = await call("engram_delete_fact", fact_id=fid, confirm=True, response_format="json")
    assert json.loads(deleted)["ok"] is True
    facts_after = await call("engram_list_facts")
    assert "No facts stored yet" in facts_after


@pytest.mark.asyncio
async def test_erase_session_tool_requires_confirmation_and_returns_receipt(local_backend):
    await call(
        "engram_remember",
        content="temporary private session marker",
        session_id="erase-me",
        scope="working",
    )

    guard = await call("engram_erase_session", session_id="erase-me")
    assert "confirm=true" in guard
    raw = await call(
        "engram_erase_session",
        session_id="erase-me",
        confirm=True,
        response_format="json",
    )
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["erasure"]["verified"] is True
    assert data["erasure"]["storage_verified"] is True
    assert data["erasure"]["counts"]["episodes"] == 1
    assert data["erasure"]["counts"]["working"] == 1


@pytest.mark.asyncio
async def test_focus_tools(local_backend):
    out = await call("engram_set_focus", track=["project decisions"], mute=["health details"])
    assert "project decisions" in out
    assert "health details" in out
    assert local_backend.get_focus("me") == {
        "track": ["project decisions"],
        "mute": ["health details"],
    }

    raw = await call("engram_get_focus", response_format="json")
    assert json.loads(raw) == {
        "track": ["project decisions"],
        "mute": ["health details"],
    }

    await call("engram_set_focus", mute=[], response_format="json")
    assert local_backend.get_focus("me") == {"track": ["project decisions"], "mute": []}

    guard = await call("engram_set_focus")
    assert "provide `track` or `mute`" in guard


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
async def test_export_tool_defaults_to_share_safe_payload(local_backend):
    await call("engram_add_fact", predicate="works_at", object="Moonshot AI", sensitive=False)
    await call("engram_add_fact", predicate="has_disease", object="diabetes", sensitive=True)

    summary = await call("engram_export")
    assert "Engram export" in summary
    assert "Include sensitive: False" in summary
    assert "Facts: 1" in summary
    assert "diabetes" not in summary.lower()

    safe = json.loads(await call("engram_export", response_format="json"))
    assert safe["include_sensitive"] is False
    assert safe["redacted_sensitive"] is True
    assert {f["object"] for f in safe["facts"]} == {"Moonshot AI"}
    assert safe["episodes"] == []
    assert safe["profile"] == ""

    full = json.loads(await call(
        "engram_export",
        include_sensitive=True,
        response_format="json",
    ))
    assert full["include_sensitive"] is True
    assert "diabetes" in {f["object"] for f in full["facts"]}


@pytest.mark.asyncio
async def test_agent_status_tool_is_content_free_and_session_aware(local_backend):
    session = "codex:super-memory:thread"
    await call(
        "engram_remember",
        content="My private diagnosis is diabetes and I work at Acme.",
        session_id=session,
    )
    local_backend.add_working("me", "today I am drafting a private launch note", session_id=session)
    await call("engram_set_focus", track=["project decisions"], mute=["health details"])

    status = await call("engram_agent_status", session_id=session)

    assert "Engram agent status" in status
    assert f"Session: `{session}`" in status
    assert "Session working memory: 1 live item" in status
    assert "project decisions" in status
    assert "engram_recall" in status
    assert "diabetes" not in status.lower()
    assert "acme" not in status.lower()
    assert "private launch note" not in status.lower()

    raw = await call("engram_agent_status", session_id=session, response_format="json")
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["session"]["working_live"] == 1
    assert data["focus"] == {"track": ["project decisions"], "mute": ["health details"]}
    assert "diabetes" not in raw.lower()
    assert "acme" not in raw.lower()


@pytest.mark.asyncio
async def test_list_sessions_tool_is_content_free(local_backend):
    codex_session = "codex:repo/thread 1"
    claude_session = "claude-code:repo/thread 2"
    await call(
        "engram_remember",
        content="Project decision: launch notes require committed raw logs.",
        session_id=codex_session,
    )
    await call(
        "engram_remember",
        content="My private diagnosis is diabetes.",
        session_id=claude_session,
    )
    local_backend.add_working("me", "temporary launch state", session_id=claude_session)

    summary = await call("engram_list_sessions", q="repo")

    assert "Memory sessions" in summary
    assert codex_session in summary
    assert claude_session in summary
    assert "diabetes" not in summary.lower()
    assert "launch notes" not in summary.lower()
    assert "temporary launch" not in summary.lower()

    raw = await call("engram_list_sessions", q="claude-code", response_format="json")
    data = json.loads(raw)
    assert data["sessions"][0]["id"] == claude_session
    assert data["sessions"][0]["working_live"] == 1
    assert data["sessions"][0]["facts_sensitive"] >= 1
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
async def test_recall_and_search_support_known_at(local_backend):
    from engram.types import Fact

    mem = local_backend.get("me")
    fact = Fact(
        "user",
        "works_at",
        "Acme",
        user_id="me",
        valid_at=10.0,
        created_at=20.0,
        embedding=mem.embedder.embed("user works at Acme"),
    )
    mem.fact_store.upsert(fact.id, fact.embedding or [], fact)
    mem.engine.graph_builder.add_fact(fact)

    visible = json.loads(await call(
        "engram_search",
        query="Where does the user work?",
        as_of=15.0,
        response_format="json",
    ))
    assert visible["answer"] == "Acme"

    hidden = json.loads(await call(
        "engram_search",
        query="Where does the user work?",
        as_of=15.0,
        known_at=15.0,
        response_format="json",
    ))
    assert hidden["known_at"] == 15.0
    assert hidden["facts"] == []


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
        session_id="s1",
        as_of=1_700_864_000.0,
        known_at=1_700_900_000.0,
        redact_sensitive=True,
    )

    url, body, headers = client.posts[-1]
    assert url == "http://engram.test/v1/recall"
    assert body["session_id"] == "s1"
    assert body["as_of"] == 1_700_864_000.0
    assert body["known_at"] == 1_700_900_000.0
    assert body["redact_sensitive"] is True
    assert headers["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_remote_backend_forwards_remember_scope():
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True, "scope": "working"}

    class FakeClient:
        def __init__(self):
            self.posts = []

        async def post(self, url, json=None, headers=None):
            self.posts.append((url, json, headers))
            return FakeResponse()

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    data = await backend.remember("temporary state", session_id="s1", scope="working")

    url, body, headers = client.posts[-1]
    assert url == "http://engram.test/v1/remember"
    assert body == {"content": "temporary state", "session_id": "s1", "scope": "working"}
    assert headers["Authorization"] == "Bearer sk-test"
    assert data["scope"] == "working"


@pytest.mark.asyncio
async def test_remote_backend_updates_and_deletes_fact():
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self):
            self.patches = []
            self.deletes = []

        async def patch(self, url, json=None, headers=None):
            self.patches.append((url, json, headers))
            return FakeResponse({"ok": True, "id": "fact/a b", "text": "user works at Moonshot AI"})

        async def delete(self, url, headers=None):
            self.deletes.append((url, headers))
            return FakeResponse({"ok": True})

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    updated = await backend.update_fact("fact/a b", object="Moonshot AI", sensitive=False)
    deleted = await backend.delete_fact("fact/a b", confirm=True)

    patch_url, body, patch_headers = client.patches[-1]
    assert patch_url == "http://engram.test/v1/facts/fact%2Fa%20b"
    assert body == {"object": "Moonshot AI", "sensitive": False}
    assert patch_headers["Authorization"] == "Bearer sk-test"
    delete_url, delete_headers = client.deletes[-1]
    assert delete_url == "http://engram.test/v1/facts/fact%2Fa%20b?confirm=true"
    assert delete_headers["Authorization"] == "Bearer sk-test"
    assert updated["ok"] is True and deleted["ok"] is True


@pytest.mark.asyncio
async def test_remote_backend_gets_and_sets_focus():
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self):
            self.gets = []
            self.puts = []

        async def get(self, url, headers=None):
            self.gets.append((url, headers))
            return FakeResponse({"track": ["project decisions"], "mute": []})

        async def put(self, url, json=None, headers=None):
            self.puts.append((url, json, headers))
            return FakeResponse({"ok": True, "focus": json})

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    current = await backend.get_focus()
    updated = await backend.set_focus(track=["project decisions"], mute=None)

    get_url, get_headers = client.gets[-1]
    assert get_url == "http://engram.test/v1/focus"
    assert get_headers["Authorization"] == "Bearer sk-test"
    put_url, body, put_headers = client.puts[-1]
    assert put_url == "http://engram.test/v1/focus"
    assert body == {"track": ["project decisions"]}
    assert put_headers["Authorization"] == "Bearer sk-test"
    assert current["track"] == ["project decisions"]
    assert updated["focus"] == {"track": ["project decisions"]}


@pytest.mark.asyncio
async def test_remote_backend_closes_session():
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True, "session_id": "s1"}

    class FakeClient:
        def __init__(self):
            self.posts = []

        async def post(self, url, json=None, headers=None):
            self.posts.append((url, json, headers))
            return FakeResponse()

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    data = await backend.close_session("s1", summarize=False, clear_working=False)

    url, body, headers = client.posts[-1]
    assert url == "http://engram.test/v1/sessions/close"
    assert body == {"session_id": "s1", "summarize": False, "clear_working": False}
    assert headers["Authorization"] == "Bearer sk-test"
    assert data["session_id"] == "s1"


@pytest.mark.asyncio
async def test_remote_backend_erases_session_with_confirmation():
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True, "erasure": {"storage_verified": True}}

    class FakeClient:
        def __init__(self):
            self.posts = []

        async def post(self, url, json=None, headers=None):
            self.posts.append((url, json, headers))
            return FakeResponse()

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    data = await backend.erase_session("private/session", confirm=True)

    url, body, headers = client.posts[-1]
    assert url == "http://engram.test/v1/sessions/erase"
    assert body == {"session_id": "private/session", "confirm": True}
    assert headers["Authorization"] == "Bearer sk-test"
    assert data["erasure"]["storage_verified"] is True


@pytest.mark.asyncio
async def test_remote_backend_fetches_session_report():
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True, "session_id": "codex:repo/thread 1", "facts": []}

    class FakeClient:
        def __init__(self):
            self.gets = []

        async def get(self, url, headers=None):
            self.gets.append((url, headers))
            return FakeResponse()

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    data = await backend.session_report("codex:repo/thread 1", include_sensitive=True)

    url, headers = client.gets[-1]
    assert url == (
        "http://engram.test/v1/sessions/report?"
        "session_id=codex%3Arepo%2Fthread+1&include_sensitive=true"
    )
    assert headers["Authorization"] == "Bearer sk-test"
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_remote_backend_routes_twin_control_plane_calls():
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def get(self, url, headers=None):
            self.calls.append(("GET", url, None, headers))
            if url.endswith("/contract"):
                return FakeResponse({"ok": True, "contract": {}, "model_context": {}})
            return FakeResponse({"ok": True, "registry": {"schema_version": 1, "grants": []}})

        async def put(self, url, json=None, headers=None):
            self.calls.append(("PUT", url, json, headers))
            return FakeResponse({"ok": True, "contract": {"version": 2}})

        async def post(self, url, json=None, headers=None):
            self.calls.append(("POST", url, json, headers))
            if url.endswith("/authorize"):
                return FakeResponse({"ok": True, "decision": {"status": "allowed"}, "executed": False})
            if url.endswith("/actions/record"):
                return FakeResponse({"ok": True, "action": {"id": "record_1"}})
            return FakeResponse({"ok": True, "grant": {"id": "grant/a b"}})

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    await backend.twin_contract()
    await backend.revise_twin_contract({"goals": [{"title": "Protect focus"}]})
    await backend.capabilities()
    await backend.grant_capability(
        "calendar",
        "execute",
        ["calendars/personal/**"],
        credential_ref={"provider": "vault", "key": "calendar/personal"},
        expires_at=42,
        provenance=["owner:grant-1"],
    )
    await backend.revoke_capability("grant/a b")
    decision = await backend.authorize_twin_action(
        "calendar",
        "execute",
        "calendars/personal/events/42",
        external_write=True,
    )
    await backend.record_twin_action(
        "decision_1",
        "Executor created the approved event",
        executed_at=43,
        provenance=["executor:calendar-1"],
    )

    assert [(method, url) for method, url, _, _ in client.calls] == [
        ("GET", "http://engram.test/v1/twin/contract"),
        ("PUT", "http://engram.test/v1/twin/contract"),
        ("GET", "http://engram.test/v1/twin/capabilities"),
        ("POST", "http://engram.test/v1/twin/capabilities"),
        ("POST", "http://engram.test/v1/twin/capabilities/grant%2Fa%20b/revoke"),
        ("POST", "http://engram.test/v1/twin/authorize"),
        ("POST", "http://engram.test/v1/twin/actions/record"),
    ]
    grant_body = client.calls[3][2]
    assert grant_body["credential_ref"] == {
        "provider": "vault",
        "key": "calendar/personal",
    }
    assert "secret" not in grant_body["credential_ref"]
    assert client.calls[4][2] == {}
    assert "human_confirmed" not in client.calls[5][2]
    assert decision["executed"] is False
    assert client.calls[6][2]["executed_at"] == 43


@pytest.mark.asyncio
async def test_remote_backend_fetches_sessions_index():
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True, "sessions": [{"id": "claude-code:repo/thread 2"}], "page": {}}

    class FakeClient:
        def __init__(self):
            self.gets = []

        async def get(self, url, headers=None):
            self.gets.append((url, headers))
            return FakeResponse()

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    data = await backend.sessions(limit=2, offset=4, q="claude-code")

    url, headers = client.gets[-1]
    assert url == "http://engram.test/v1/sessions?limit=2&offset=4&q=claude-code"
    assert headers["Authorization"] == "Bearer sk-test"
    assert data["sessions"][0]["id"] == "claude-code:repo/thread 2"


@pytest.mark.asyncio
async def test_remote_backend_exports_memory():
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"engram_export_version": 1, "include_sensitive": False, "facts": []}

    class FakeClient:
        def __init__(self):
            self.gets = []

        async def get(self, url, headers=None):
            self.gets.append((url, headers))
            return FakeResponse()

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    safe = await backend.export()
    full = await backend.export(include_sensitive=True)

    assert client.gets[0][0] == "http://engram.test/v1/export?include_sensitive=false"
    assert client.gets[0][1]["Authorization"] == "Bearer sk-test"
    assert client.gets[1][0] == "http://engram.test/v1/export?include_sensitive=true"
    assert safe["engram_export_version"] == 1
    assert full["engram_export_version"] == 1


@pytest.mark.asyncio
async def test_remote_backend_forget_sends_confirm():
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True, "message": "All memory erased."}

    class FakeClient:
        def __init__(self):
            self.posts = []

        async def post(self, url, json=None, headers=None):
            self.posts.append((url, json, headers))
            return FakeResponse()

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    data = await backend.forget()

    url, body, headers = client.posts[-1]
    assert url == "http://engram.test/v1/forget"
    assert body == {"confirm": True}
    assert headers["Authorization"] == "Bearer sk-test"
    assert data["ok"] is True


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
async def test_remote_backend_fetches_agent_status():
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True, "session_id": "codex:repo:thread"}

    class FakeClient:
        def __init__(self):
            self.gets = []

        async def get(self, url, headers=None):
            self.gets.append((url, headers))
            return FakeResponse()

    client = FakeClient()
    backend = RemoteBackend("http://engram.test", key="sk-test", client=client)
    data = await backend.agent_status(session_id="codex:repo:thread")

    url, headers = client.gets[-1]
    assert url == "http://engram.test/v1/agent/status?session_id=codex%3Arepo%3Athread"
    assert headers["Authorization"] == "Bearer sk-test"
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_forget_requires_confirm(local_backend):
    await call("engram_remember", content="ephemeral note")
    guard = await call("engram_forget")  # no confirm
    assert "confirm=true" in guard
    done = await call("engram_forget", confirm=True)
    assert "erased" in done.lower()
    facts = await call("engram_list_facts")
    assert "No facts stored yet" in facts
