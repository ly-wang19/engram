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


def test_remember_recall_roundtrip(client):
    h = hdr("alice")
    assert client.post("/v1/remember", json={"content": "My name is Wei and I live in Shenzhen."},
                       headers=h).json()["ok"] is True
    dump = client.get("/v1/memories", headers=h).json()
    assert dump["counts"]["episodes"] >= 1
    rec = client.post("/v1/recall", json={"query": "Where does the user live?"}, headers=h).json()
    assert "Shenzhen" in rec["context"] and rec["tokens_est"] > 0


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

    full = client.get("/v1/export", headers=h).json()
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
    assert client.post("/v1/forget", headers=h).json()["ok"] is True
    assert client.get("/v1/memories", headers=h).json()["counts"]["episodes"] == 0


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
