"""HTTP API smoke tests over the refactored MemoryService-backed app — offline (hashing embedder,
rule extractor), so no model download or API key is needed. Exercises every route shape the React
console + SDK depend on, plus the new /v1/import."""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
    d = tempfile.mkdtemp(prefix="engram_srv_")
    os.environ.update(ENGRAM_DATA_DIR=d, ENGRAM_EMBEDDER="hashing", ENGRAM_OPEN="1")
    os.environ.pop("ENGRAM_LLM", None)
    os.environ.pop("ENGRAM_API_KEYS", None)
    os.environ.pop("ENGRAM_ALLOW_ANONYMOUS", None)
    from engram.server import app as appmod
    appmod._svc = None  # fresh singleton bound to the test env
    with TestClient(appmod.app) as c:
        yield c
    shutil.rmtree(d, ignore_errors=True)


def hdr(ns: str) -> dict:
    return {"Authorization": f"Bearer {ns}"}  # open mode -> the bearer text IS the namespace


def test_health(client):
    r = client.get("/health")
    data = r.json()
    assert r.status_code == 200 and data["ok"] is True and data["ready"] is True
    assert data["auth_mode"] == "open"
    assert data["anonymous_allowed"] is False
    assert data["embedder"] == "HashingEmbedder"
    assert data["llm_configured"] is False
    assert data["answerer_configured"] is False
    assert data["storage"] == "memory"
    assert data["max_hot_users"] >= data["users_hot"]
    assert data["max_hot_facts"] >= 1
    assert "data_dir" not in data and "api_keys" not in data


def test_private_api_responses_are_not_cacheable(client):
    h = hdr("cache_headers")

    memories = client.get("/v1/memories", headers=h)
    exported = client.get("/v1/export", headers=h)

    assert memories.headers["cache-control"] == "no-store"
    assert exported.headers["cache-control"] == "no-store"


def test_remember_recall_roundtrip(client):
    h = hdr("alice")
    assert client.post("/v1/remember", json={"content": "My name is Wei and I live in Shenzhen."},
                       headers=h).json()["ok"] is True
    dump = client.get("/v1/memories", headers=h).json()
    assert dump["counts"]["episodes"] >= 1
    rec = client.post("/v1/recall", json={"query": "Where does the user live?"}, headers=h).json()
    assert "Shenzhen" in rec["context"] and rec["tokens_est"] > 0


def test_close_session_endpoint_consolidates_summarizes_and_clears_working(client):
    h = hdr("session_close")
    client.post("/v1/import", json={
        "format": "records",
        "consolidate": False,
        "summarize": False,
        "data": [{"session_id": "s1", "content": "I work at Acme."}],
    }, headers=h)
    client.post("/v1/working", json={
        "content": "today I am checking the release notes",
        "session_id": "s1",
    }, headers=h)

    out = client.post("/v1/sessions/close", json={"session_id": "s1"}, headers=h).json()

    assert out["ok"] is True
    assert out["session_id"] == "s1"
    assert out["episodes"] == 1
    assert out["pending_consolidated"] == 1
    assert out["facts_added"] >= 1
    assert out["summaries"] == 1
    assert out["working_cleared"] == 1
    assert client.get("/v1/working?session_id=s1", headers=h).json()["items"] == []
    assert client.get("/v1/memories", headers=h).json()["counts"]["summaries"] == 1


def test_http_handoff_across_agent_sessions_in_same_namespace(client):
    h = hdr("agent_handoff")
    codex_session = "codex:super-memory:handoff-source"
    claude_session = "claude-code:super-memory:handoff-target"

    written = client.post("/v1/remember", json={
        "content": "Project decision: the launch checklist must include committed eval logs.",
        "session_id": codex_session,
        "scope": "long",
    }, headers=h).json()
    assert written["ok"] is True
    assert written["extracted"] >= 1
    assert client.post("/v1/sessions/close", json={"session_id": codex_session}, headers=h).json()["ok"] is True

    recalled = client.post("/v1/recall", json={
        "query": "What launch checklist decision did Codex record?",
        "session_id": claude_session,
        "n_chunks": 3,
    }, headers=h).json()

    assert "committed eval logs" in recalled["context"]
    assert codex_session in recalled["context"]
    assert client.get(f"/v1/working?session_id={claude_session}", headers=h).json()["items"] == []


def test_session_report_endpoint_audits_saved_facts(client):
    h = hdr("session_report")
    session = "codex:super-memory:thread"
    client.post("/v1/remember", json={
        "content": "Project decision: the launch checklist must include committed eval logs.",
        "session_id": session,
        "scope": "long",
    }, headers=h)
    client.post("/v1/sessions/close", json={"session_id": session}, headers=h)

    report = client.get(f"/v1/sessions/report?session_id={session}", headers=h).json()

    assert report["ok"] is True
    assert report["session_id"] == session
    assert report["episodes"] == 1
    assert report["episodes_pending"] == 0
    assert report["facts_added"] >= 1
    assert "committed eval logs" in str(report)


def test_sessions_endpoint_lists_cross_agent_sessions_without_content(client):
    h = hdr("sessions_index")
    codex_session = "codex:super-memory:thread-1"
    claude_session = "claude-code:super-memory:thread-2"
    client.post("/v1/remember", json={
        "content": "Project decision: release notes require committed raw logs.",
        "session_id": codex_session,
        "scope": "long",
    }, headers=h)
    client.post("/v1/remember", json={
        "content": "My private diagnosis is diabetes.",
        "session_id": claude_session,
        "scope": "long",
    }, headers=h)
    client.post("/v1/working", json={
        "content": "temporary launch checklist state",
        "session_id": claude_session,
    }, headers=h)

    data = client.get("/v1/sessions?limit=10", headers=h).json()

    assert data["ok"] is True
    rows = {row["id"]: row for row in data["sessions"]}
    assert {codex_session, claude_session} <= set(rows)
    assert rows[codex_session]["episodes"] == 1
    assert rows[codex_session]["facts_added"] >= 1
    assert rows[claude_session]["working_live"] == 1
    assert rows[claude_session]["facts_sensitive"] >= 1
    assert data["page"]["total"] >= 2
    rendered = str(data).lower()
    assert "release notes" not in rendered
    assert "diabetes" not in rendered
    assert "temporary launch" not in rendered

    filtered = client.get("/v1/sessions?q=claude-code&limit=1", headers=h).json()
    assert filtered["page"]["total"] == 1
    assert filtered["sessions"][0]["id"] == claude_session


def test_stats_endpoint_is_content_free_namespace_observability(client):
    h = hdr("stats")
    client.post("/v1/remember", json={
        "content": "My private diagnosis is diabetes. I work at Acme.",
        "scope": "long",
    }, headers=h)
    client.post("/v1/facts", json={"predicate": "has_disease", "object": "diabetes"}, headers=h)
    client.post("/v1/facts", json={"predicate": "works_at", "object": "Acme"}, headers=h)

    stats = client.get("/v1/stats", headers=h).json()
    assert stats["user"] == "stats"
    assert stats["counts"]["episodes"] >= 1
    assert stats["counts"]["episodes_consolidated"] >= 1
    assert stats["counts"]["episodes_pending"] == 0
    assert stats["counts"]["episodes_ephemeral"] == 0
    assert stats["counts"]["facts_hot"] >= 2
    assert stats["counts"]["facts_cold"] == 0
    assert stats["counts"]["facts_live"] >= 2
    assert stats["counts"]["facts_sensitive"] >= 1
    assert stats["counts"]["entities"] >= 1
    assert stats["counts"]["graph_orphan_entities"] == 0
    assert stats["counts"]["graph_stale_relations"] == 0
    assert stats["time_range"]["first_event_at"] is not None
    assert stats["storage"] == "memory"
    assert stats["max_hot_facts"] >= 1
    assert stats["embedder"] == "HashingEmbedder"
    assert stats["consolidation_backlog"] is False
    rendered = str(stats).lower()
    assert "diabetes" not in rendered
    assert "acme" not in rendered
    assert "private diagnosis" not in rendered
    assert "data_dir" not in stats and "profile" not in stats and "facts" not in stats


def test_agent_status_endpoint_is_content_free_and_session_aware(client):
    h = hdr("agent_status")
    session = "codex:super-memory:thread"
    client.post("/v1/remember", json={
        "content": "My private diagnosis is diabetes. I work at Acme.",
        "session_id": session,
        "scope": "long",
    }, headers=h)
    client.post("/v1/working", json={
        "content": "today I am preparing a private launch note",
        "session_id": session,
    }, headers=h)
    client.put("/v1/focus", json={
        "track": ["project decisions"],
        "mute": ["health details"],
    }, headers=h)

    status = client.get(f"/v1/agent/status?session_id={session}", headers=h).json()

    assert status["ok"] is True
    assert status["user"] == "agent_status"
    assert status["session_id"] == session
    assert status["session"]["episodes"] == 1
    assert status["session"]["working_live"] == 1
    assert status["focus"] == {"track": ["project decisions"], "mute": ["health details"]}
    assert status["counts"]["facts_live"] >= 1
    assert "engram_recall" in status["tools"]["read_context"]
    rendered = str(status).lower()
    assert "diabetes" not in rendered
    assert "acme" not in rendered
    assert "private launch note" not in rendered


def test_import_sessions_and_raw_data(client):
    h = hdr("importer")
    sessions = [{"session_id": "s1", "title": "Pets",
                 "messages": [{"role": "user", "content": "I have a dog named Mochi."},
                              {"role": "assistant", "content": "Mochi is a cute name!"}]}]
    out = client.post("/v1/import", json={"sessions": sessions}, headers=h).json()
    assert out["ok"] and out["episodes"] == 1
    # raw data + format path
    out2 = client.post("/v1/import", json={
        "data": [{"role": "user", "content": "I drive a Tesla Model 3."}], "format": "messages"}, headers=h).json()
    assert out2["episodes"] == 1
    ctx = client.post("/v1/recall", json={"query": "What pet do I have?"}, headers=h).json()["context"]
    assert "Mochi" in ctx
    # empty import request is a clear 400
    assert client.post("/v1/import", json={}, headers=h).status_code == 400


def test_import_sessions_accept_iso_timestamps(client):
    h = hdr("import_iso")
    out = client.post("/v1/import", json={
        "sessions": [{
            "session_id": "career",
            "messages": [
                {
                    "role": "user",
                    "content": "Wei works at Tencent.",
                    "event_time": "2023-11-14T00:00:00Z",
                },
                {
                    "role": "user",
                    "content": "Wei works at Moonshot AI.",
                    "event_time": "2023-12-14T00:00:00Z",
                },
            ],
        }],
        "summarize": False,
    }, headers=h).json()

    assert out["ok"] and out["sessions"] == 1 and out["episodes"] == 2
    memories = client.get("/v1/memories", headers=h).json()
    facts = memories["facts"]
    assert {f["object"] for f in facts} >= {"Tencent", "Moonshot AI"}
    past = client.post("/v1/recall", json={
        "query": "Where does Wei work?",
        "lean": False,
        "as_of": 1_700_864_000.0,
    }, headers=h).json()
    assert "Tencent" in past["answer"]


def test_fact_crud_and_focus_policy_graph_export(client):
    h = hdr("editor")
    fid = client.post("/v1/facts", json={"predicate": "works_at", "object": "Tencent"}, headers=h).json()["id"]
    assert client.patch(f"/v1/facts/{fid}", json={"object": "Moonshot AI"}, headers=h).json()["ok"]
    assert client.patch("/v1/facts/nope", json={"object": "x"}, headers=h).status_code == 404
    # focus + policy round-trip through the service
    assert client.put("/v1/focus", json={"track": ["career"], "mute": []}, headers=h).json()["focus"]["track"] == ["career"]
    assert client.get("/v1/focus", headers=h).json()["track"] == ["career"]
    pol = client.put("/v1/policy", json={"extract_instruction": "Record job changes."}, headers=h).json()
    assert pol["ok"] and pol["policy"]["extract_instruction"] == "Record job changes."
    assert "nodes" in client.get("/v1/graph", headers=h).json()
    exp = client.get("/v1/export", headers=h).json()
    assert exp["engram_export_version"] == 1 and any(f["object"] == "Moonshot AI" for f in exp["facts"])
    assert client.delete(f"/v1/facts/{fid}", headers=h).json()["ok"] is True


def test_graph_endpoint_supports_as_of_and_edge_audit_fields(client):
    h = hdr("graph_asof")
    client.post("/v1/import", json={
        "format": "records",
        "data": [
            {"session_id": "old", "content": "Wei works at Tencent.", "event_time": 1_700_000_000.0},
            {"session_id": "new", "content": "Wei works at Moonshot AI.", "event_time": 1_702_592_000.0},
        ],
    }, headers=h)

    past = client.get("/v1/graph?as_of=1700864000", headers=h).json()
    assert "Tencent" in {n["name"] for n in past["nodes"]}
    assert "Moonshot AI" not in {n["name"] for n in past["nodes"]}
    assert past["edges"] and all(e["live"] for e in past["edges"])

    edge = past["edges"][0]
    assert edge["fact_id"] and edge["fact_text"]
    assert edge["valid_at"] == 1_700_000_000.0
    assert edge["valid_at_h"]
    assert "invalid_at" in edge and "invalid_at_h" in edge
    assert isinstance(edge["provenance"], list)


def test_graph_endpoint_can_exclude_sensitive_edges(client):
    h = hdr("graph_redact")
    client.post("/v1/facts", json={"predicate": "has_disease", "object": "diabetes"}, headers=h)
    client.post("/v1/facts", json={"predicate": "works_at", "object": "Acme"}, headers=h)

    full = client.get("/v1/graph", headers=h).json()
    assert "diabetes" in str(full).lower()
    assert "Acme" in str(full)

    safe = client.get("/v1/graph?include_sensitive=false", headers=h).json()
    rendered = str(safe).lower()
    assert "diabetes" not in rendered
    assert "Acme" in str(safe)
    assert all("diabetes" not in e.get("fact_text", "").lower() for e in safe["edges"])


def test_memories_endpoint_supports_pagination_filtering_and_sensitive_create(client):
    h = hdr("memories_page")
    client.post("/v1/remember", json={"content": "First durable episode."}, headers=h)
    client.post("/v1/remember", json={"content": "Second durable episode."}, headers=h)
    client.post("/v1/facts", json={
        "predicate": "works_at",
        "object": "Acme",
        "category": "career",
    }, headers=h)
    client.post("/v1/facts", json={
        "predicate": "favorite_city",
        "object": "Hangzhou",
        "category": "preference",
    }, headers=h)
    client.post("/v1/facts", json={
        "predicate": "project_note",
        "object": "memory console",
        "category": "product",
    }, headers=h)
    client.post("/v1/facts", json={
        "predicate": "security_phrase",
        "object": "blue-lotus",
        "category": "security",
        "sensitive": True,
    }, headers=h)

    episode_page = client.get("/v1/memories?facts_limit=0&episodes_limit=1", headers=h).json()
    assert len(episode_page["episodes"]) == 1
    assert episode_page["episodes_page"]["total"] == 2
    assert episode_page["episodes_page"]["has_more"] is True
    assert episode_page["next_offsets"]["episodes"] == episode_page["episodes_page"]["next_offset"]

    page = client.get(
        "/v1/memories?facts_limit=2&facts_offset=0&episodes_limit=1&include_sensitive=false",
        headers=h,
    ).json()
    assert len(page["facts"]) == 2
    assert page["facts_page"]["total"] >= 3
    assert page["facts_page"]["has_more"] is True
    assert page["facts_page"]["next_offset"] == 2
    assert page["episodes"] == []
    assert page["episodes_page"]["total"] == 0
    assert page["episodes_page"]["has_more"] is False
    assert page["next_offsets"] == {
        "facts": page["facts_page"]["next_offset"],
        "episodes": page["episodes_page"]["next_offset"],
    }
    assert "blue-lotus" not in str(page).lower()

    second = client.get(
        "/v1/memories?facts_limit=2&facts_offset=2&include_sensitive=false",
        headers=h,
    ).json()
    assert second["facts_page"]["offset"] == 2
    assert all(not f["sensitive"] for f in second["facts"])

    by_category = client.get(
        "/v1/memories?q=security&include_sensitive=true&episodes_limit=0",
        headers=h,
    ).json()
    assert any(f["object"] == "blue-lotus" and f["sensitive"] for f in by_category["facts"])

    safe_search = client.get(
        "/v1/memories?q=blue-lotus&include_sensitive=false&episodes_limit=0",
        headers=h,
    ).json()
    assert safe_search["facts_page"]["total"] == 0
    assert safe_search["facts"] == []


def test_graph_endpoint_supports_query_live_only_and_limit(client):
    h = hdr("graph_query")
    client.post("/v1/facts", json={"predicate": "works_at", "object": "Acme"}, headers=h)
    client.post("/v1/facts", json={"predicate": "works_at", "object": "Moonshot AI"}, headers=h)
    client.post("/v1/facts", json={"predicate": "likes_food", "object": "ramen"}, headers=h)

    old_company = client.get("/v1/graph?q=Acme&live_only=true", headers=h).json()
    assert "Acme" not in str(old_company)

    current_company = client.get("/v1/graph?q=Moonshot&live_only=true&limit=1", headers=h).json()
    assert len(current_company["edges"]) <= 1
    assert "Moonshot AI" in str(current_company)
    assert all(edge["live"] for edge in current_company["edges"])


def test_recall_endpoint_supports_as_of_for_search_and_lean_context(client):
    h = hdr("recall_asof")
    client.post("/v1/import", json={
        "format": "records",
        "data": [
            {"session_id": "old", "content": "Wei works at Tencent.", "event_time": 1_700_000_000.0},
            {"session_id": "new", "content": "Wei works at Moonshot AI.", "event_time": 1_702_592_000.0},
        ],
    }, headers=h)

    past = client.post("/v1/recall", json={
        "query": "Where does Wei work?",
        "lean": False,
        "as_of": 1_700_864_000.0,
    }, headers=h).json()
    assert past["as_of"] == 1_700_864_000.0
    assert "Tencent" in past["answer"]
    assert all("Moonshot" not in f for f in past["facts"])

    current = client.post("/v1/recall", json={
        "query": "Where does Wei work?",
        "lean": False,
    }, headers=h).json()
    assert current["as_of"] is None
    assert "Moonshot AI" in current["answer"]

    ctx = client.post("/v1/recall", json={
        "query": "Where does Wei work?",
        "lean": True,
        "n_chunks": 0,
        "as_of": 1_700_864_000.0,
    }, headers=h).json()
    assert ctx["as_of"] == 1_700_864_000.0
    assert "Tencent" in ctx["context"]
    assert "Moonshot AI" not in ctx["context"]

    current_ctx = client.post("/v1/recall", json={
        "query": "Where does Wei work?",
        "lean": True,
        "n_chunks": 0,
    }, headers=h).json()
    assert current_ctx["as_of"] is None
    assert current_ctx["full_tokens"] > ctx["full_tokens"]


def test_recall_endpoint_can_redact_sensitive_facts(client):
    h = hdr("recall_redact")
    client.post("/v1/facts", json={"predicate": "has_disease", "object": "diabetes"}, headers=h)
    client.post("/v1/facts", json={"predicate": "works_at", "object": "Acme"}, headers=h)

    ctx = client.post("/v1/recall", json={
        "query": "what do you know about me?",
        "lean": True,
        "n_chunks": 0,
        "redact_sensitive": True,
    }, headers=h).json()
    assert ctx["redacted_sensitive"] is True
    assert "Acme" in ctx["context"]
    assert "diabetes" not in ctx["context"].lower()

    direct = client.post("/v1/recall", json={
        "query": "what disease do I have?",
        "lean": False,
        "redact_sensitive": True,
    }, headers=h).json()
    assert direct["redacted_sensitive"] is True
    assert "diabetes" not in direct["answer"].lower()
    assert all("diabetes" not in fact.lower() for fact in direct["facts"])


def test_export_without_sensitive_omits_free_text_layers_and_sensitive_graph(client):
    h = hdr("export_redact")
    client.post("/v1/remember", json={
        "content": "My private diagnosis is diabetes. I work at Acme.",
    }, headers=h)
    client.post("/v1/facts", json={"predicate": "has_disease", "object": "diabetes"}, headers=h)
    client.post("/v1/facts", json={"predicate": "works_at", "object": "Acme"}, headers=h)

    default_safe = client.get("/v1/export", headers=h).json()
    assert default_safe["include_sensitive"] is False
    assert default_safe["redacted_sensitive"] is True
    assert "diabetes" not in str(default_safe).lower()
    assert default_safe["profile"] == ""
    assert default_safe["episodes"] == []

    full = client.get("/v1/export?include_sensitive=true", headers=h).json()
    assert full["include_sensitive"] is True
    assert any("diabetes" in f["text"].lower() for f in full["facts"])
    assert full["episodes"]

    safe = client.get("/v1/export?include_sensitive=false", headers=h).json()
    rendered = str(safe).lower()
    assert safe["include_sensitive"] is False
    assert safe["redacted_sensitive"] is True
    assert safe["profile"] == ""
    assert safe["episodes"] == []
    assert "diabetes" not in rendered
    assert any(f["object"] == "Acme" for f in safe["facts"])
    assert all("diabetes" not in e.get("fact_text", "").lower() for e in safe["graph"]["edges"])


def test_forget_clears_namespace(client):
    h = hdr("ephemeral")
    client.post("/v1/remember", json={"content": "Temporary note."}, headers=h)
    guard = client.post("/v1/forget", headers=h)
    assert guard.status_code == 400
    assert "confirm=true" in guard.json()["detail"]
    assert client.get("/v1/memories", headers=h).json()["counts"]["episodes"] == 1
    assert client.post("/v1/forget", json={"confirm": True}, headers=h).json()["ok"] is True
    assert client.get("/v1/memories", headers=h).json()["counts"]["episodes"] == 0


def test_forget_rejects_public_demo_and_anonymous_namespaces(client):
    assert client.post("/v1/forget", headers=hdr("1")).status_code == 403

    d = tempfile.mkdtemp(prefix="engram_anon_forget_")
    saved = {k: os.environ.get(k) for k in [
        "ENGRAM_DATA_DIR",
        "ENGRAM_EMBEDDER",
        "ENGRAM_OPEN",
        "ENGRAM_API_KEYS",
        "ENGRAM_ALLOW_ANONYMOUS",
    ]}
    os.environ.update(
        ENGRAM_DATA_DIR=d,
        ENGRAM_EMBEDDER="hashing",
        ENGRAM_OPEN="1",
        ENGRAM_ALLOW_ANONYMOUS="1",
    )
    os.environ.pop("ENGRAM_API_KEYS", None)
    try:
        from engram.server import app as appmod
        appmod._svc = None
        with TestClient(appmod.app) as c:
            assert c.post("/v1/forget").status_code == 403
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        from engram.server import app as appmod
        appmod._svc = None
        shutil.rmtree(d, ignore_errors=True)


def test_static_cache_headers_and_robots(client):
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "Disallow: /ui" in robots.text
    assert "Disallow: /v1/" in robots.text

    root = client.get("/", follow_redirects=False)
    assert root.headers["Cache-Control"] == "no-cache"

    from engram.server import app as appmod
    if not appmod._spa_built():
        pytest.skip("SPA dist is not built")
    asset_dir = os.path.join(appmod._DIST, "assets")
    asset = sorted(os.listdir(asset_dir))[0]
    ui_head = client.head("/ui")
    assert ui_head.status_code == 200
    assert ui_head.headers["Cache-Control"] == "no-cache"
    resp = client.get(f"/ui/assets/{asset}")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_auth_rejects_when_keys_configured():
    """With ENGRAM_API_KEYS set and no open mode, a bad key is 401."""
    d = tempfile.mkdtemp(prefix="engram_auth_")
    os.environ.update(ENGRAM_DATA_DIR=d, ENGRAM_EMBEDDER="hashing", ENGRAM_API_KEYS="alice:sk-good")
    os.environ.pop("ENGRAM_OPEN", None)
    try:
        from engram.server import app as appmod
        appmod._svc = None
        with TestClient(appmod.app) as c:
            health = c.get("/health").json()
            assert health["auth_mode"] == "api_keys"
            assert health["anonymous_allowed"] is False
            assert c.post("/v1/recall", json={"query": "hi"}, headers=hdr("sk-bad")).status_code == 401
            malformed = c.post(
                "/v1/recall",
                json={"query": "hi"},
                headers={"Authorization": "Basic sk-good"},
            )
            assert malformed.status_code == 401
            assert "Authorization: Bearer <token>" in malformed.json()["detail"]
            assert c.post("/v1/recall", json={"query": "hi"},
                          headers={"Authorization": "Bearer sk-good"}).status_code == 200
    finally:
        # restore open mode for any later modules
        os.environ.pop("ENGRAM_API_KEYS", None)
        os.environ["ENGRAM_OPEN"] = "1"
        shutil.rmtree(d, ignore_errors=True)


def test_open_mode_requires_bearer_namespace_by_default():
    d = tempfile.mkdtemp(prefix="engram_open_auth_")
    os.environ.update(ENGRAM_DATA_DIR=d, ENGRAM_EMBEDDER="hashing", ENGRAM_OPEN="1")
    os.environ.pop("ENGRAM_API_KEYS", None)
    os.environ.pop("ENGRAM_ALLOW_ANONYMOUS", None)
    try:
        from engram.server import app as appmod
        appmod._svc = None
        with TestClient(appmod.app) as c:
            missing = c.post("/v1/recall", json={"query": "hi"})
            assert missing.status_code == 401
            assert "Bearer <namespace>" in missing.json()["detail"]
            malformed = c.post(
                "/v1/recall",
                json={"query": "hi"},
                headers={"Authorization": "Basic demo"},
            )
            assert malformed.status_code == 401
            assert "Authorization: Bearer <token>" in malformed.json()["detail"]
            assert c.post("/v1/recall", json={"query": "hi"}, headers=hdr("demo")).status_code == 200
    finally:
        os.environ["ENGRAM_OPEN"] = "1"
        os.environ.pop("ENGRAM_API_KEYS", None)
        os.environ.pop("ENGRAM_ALLOW_ANONYMOUS", None)
        shutil.rmtree(d, ignore_errors=True)


def test_open_mode_allows_anonymous_only_when_explicitly_enabled():
    d = tempfile.mkdtemp(prefix="engram_open_anon_")
    os.environ.update(
        ENGRAM_DATA_DIR=d,
        ENGRAM_EMBEDDER="hashing",
        ENGRAM_OPEN="1",
        ENGRAM_ALLOW_ANONYMOUS="1",
    )
    os.environ.pop("ENGRAM_API_KEYS", None)
    try:
        from engram.server import app as appmod
        appmod._svc = None
        with TestClient(appmod.app) as c:
            assert c.post("/v1/recall", json={"query": "hi"}).status_code == 200
    finally:
        os.environ["ENGRAM_OPEN"] = "1"
        os.environ.pop("ENGRAM_API_KEYS", None)
        os.environ.pop("ENGRAM_ALLOW_ANONYMOUS", None)
        shutil.rmtree(d, ignore_errors=True)
