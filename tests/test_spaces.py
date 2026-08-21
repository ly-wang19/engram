"""Multi-space (multi-agent / team-shared) memory tests (CLAUDE.md §6). Cover the ENGRAM_SPACES ACL
resolution, the service-level cross-space read fusion, and the HTTP write/read authorization. Single-space
auth (ENGRAM_API_KEYS / open mode) is untouched by design and stays covered by test_server. All offline
(hashing embedder, no LLM); env via monkeypatch so it auto-reverts."""
from __future__ import annotations

import json
import shutil
import tempfile

import pytest

from engram.service import MemoryService
from engram.spaces import Principal, principal_for_token


# --- ACL resolution ---------------------------------------------------------


def test_spaces_config_defaults_write_to_home(monkeypatch):
    monkeypatch.setenv("ENGRAM_SPACES", json.dumps({"sk": {"home": "h", "read": ["h", "t"]}}))
    p = principal_for_token("sk")
    assert p is not None and p.home == "h"
    assert p.can_read("h") and p.can_read("t")
    assert p.can_write("h") and not p.can_write("t")  # write omitted -> defaults to [home]
    assert principal_for_token("other") is None  # unknown token -> not a spaces principal


def test_malformed_spaces_is_ignored(monkeypatch):
    monkeypatch.setenv("ENGRAM_SPACES", "{not valid json")
    assert principal_for_token("whatever") is None  # broken config must not grant access


def test_single_principal_shape():
    p = Principal.single("alice")
    assert p.home == "alice" and p.can_read("alice") and p.can_write("alice")
    assert not p.can_read("bob")


# --- service-level cross-space fusion ---------------------------------------


def test_recall_multi_fuses_spaces():
    d = tempfile.mkdtemp(prefix="engram_sp_")
    try:
        svc = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        svc.remember("agentA", "The deploy key rotates every 90 days.")
        svc.remember("team", "Standup is at 10am daily.")
        out = svc.recall_multi(["agentA", "team"], "standup")
        assert out["spaces"] == ["agentA", "team"]
        # both spaces contributed, each tagged with its source
        assert "Space: agentA" in out["context"] and "Space: team" in out["context"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --- HTTP write/read authorization ------------------------------------------


@pytest.fixture()
def spaces_client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    d = tempfile.mkdtemp(prefix="engram_spsrv_")
    cfg = {
        "sk-agent": {"home": "agentA", "read": ["agentA", "team"], "write": ["agentA", "team"]},
        "sk-alice": {"home": "alice"},  # read/write default to [home] only
    }
    monkeypatch.setenv("ENGRAM_DATA_DIR", d)
    monkeypatch.setenv("ENGRAM_EMBEDDER", "hashing")
    monkeypatch.setenv("ENGRAM_SPACES", json.dumps(cfg))
    for var in ("ENGRAM_API_KEYS", "ENGRAM_OPEN", "ENGRAM_ALLOW_ANONYMOUS", "ENGRAM_LLM"):
        monkeypatch.delenv(var, raising=False)
    from engram.server import app as appmod
    appmod._svc = None
    with TestClient(appmod.app) as c:
        yield c
    appmod._svc = None
    shutil.rmtree(d, ignore_errors=True)


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_write_home_and_shared_then_read_fuses(spaces_client):
    c = spaces_client
    assert c.post("/v1/remember", json={"content": "Agent A note: the API base is api.acme.dev."},
                  headers=_h("sk-agent")).json()["ok"] is True
    assert c.post("/v1/remember", json={"content": "Team standup is daily at 10am.", "space": "team"},
                  headers=_h("sk-agent")).json()["ok"] is True
    r = c.post("/v1/recall", json={"query": "standup", "spaces": ["agentA", "team"]},
               headers=_h("sk-agent")).json()
    assert r["spaces"] == ["agentA", "team"]
    assert "Space: agentA" in r["context"] and "Space: team" in r["context"]


def test_write_to_unwritable_space_is_forbidden(spaces_client):
    r = spaces_client.post("/v1/remember", json={"content": "x", "space": "team"}, headers=_h("sk-alice"))
    assert r.status_code == 403


def test_read_unreadable_space_is_forbidden(spaces_client):
    r = spaces_client.post("/v1/recall", json={"query": "x", "spaces": ["team"]}, headers=_h("sk-alice"))
    assert r.status_code == 403


def test_unknown_token_is_unauthorized(spaces_client):
    # not a spaces key, no ENGRAM_API_KEYS, no open mode -> the fallback single-space auth rejects it
    assert spaces_client.post("/v1/recall", json={"query": "x"}, headers=_h("sk-nope")).status_code == 401


def test_home_default_single_space_answer_shape(spaces_client):
    c = spaces_client
    c.post("/v1/remember", json={"content": "Alice lives in Berlin."}, headers=_h("sk-alice"))
    r = c.post("/v1/recall", json={"query": "Where does the user live?"}, headers=_h("sk-alice")).json()
    # the single-space path keeps the existing recall(answer=True) shape (context + token estimate)
    assert "context" in r and "tokens_est" in r
