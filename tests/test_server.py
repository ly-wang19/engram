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
    from engram.server import app as appmod
    appmod._svc = None  # fresh singleton bound to the test env
    with TestClient(appmod.app) as c:
        yield c
    shutil.rmtree(d, ignore_errors=True)


def hdr(ns: str) -> dict:
    return {"Authorization": f"Bearer {ns}"}  # open mode -> the bearer text IS the namespace


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_remember_recall_roundtrip(client):
    h = hdr("alice")
    assert client.post("/v1/remember", json={"content": "My name is Wei and I live in Shenzhen."},
                       headers=h).json()["ok"] is True
    dump = client.get("/v1/memories", headers=h).json()
    assert dump["counts"]["episodes"] >= 1
    rec = client.post("/v1/recall", json={"query": "Where does the user live?"}, headers=h).json()
    assert "Shenzhen" in rec["context"] and rec["tokens_est"] > 0


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
            assert c.post("/v1/recall", json={"query": "hi"}, headers=hdr("sk-bad")).status_code == 401
            assert c.post("/v1/recall", json={"query": "hi"},
                          headers={"Authorization": "Bearer sk-good"}).status_code == 200
    finally:
        # restore open mode for any later modules
        os.environ.pop("ENGRAM_API_KEYS", None)
        os.environ["ENGRAM_OPEN"] = "1"
        shutil.rmtree(d, ignore_errors=True)
