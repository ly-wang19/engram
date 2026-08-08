"""OpenAI-compatible /v1/chat/completions — memory injection + generation + background remember.
Offline: hashing embedder, rule extractor, and a deterministic FakeLLM for generation."""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from engram.llm import FakeLLM
from engram.server import openai_compat as oc
from engram.service import MemoryService

# --- pure assembly logic (no web stack) -------------------------------------


def test_latest_user_text_and_multimodal():
    msgs = [{"role": "system", "content": "be nice"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": [{"type": "text", "text": "where do I live?"}]}]
    assert oc.latest_user_text(msgs) == "where do I live?"


def test_build_prompt_injects_memory_and_system():
    msgs = [{"role": "system", "content": "house rules"},
            {"role": "user", "content": "hi"}]
    system, prompt = oc.build_prompt(msgs, "FACTS: user lives in Berlin")
    assert "RELEVANT MEMORY" in system and "Berlin" in system and "house rules" in system
    assert prompt == "hi"
    # multi-turn renders a transcript
    msgs2 = msgs + [{"role": "assistant", "content": "hello"}, {"role": "user", "content": "more"}]
    _, prompt2 = oc.build_prompt(msgs2, "")
    assert "User:" in prompt2 and prompt2.endswith("Assistant:")


def test_iter_sse_reassembles_exactly():
    resp = {"id": "x", "created": 0, "model": "engram",
            "choices": [{"message": {"content": "Hello, 世界! " * 10}}]}
    body = "".join(p for p in oc.iter_sse(resp))
    assert body.endswith("data: [DONE]\n\n")
    # the concatenated deltas reconstruct the original content
    import json
    rebuilt = ""
    for line in body.splitlines():
        if line.startswith("data: ") and "[DONE]" not in line:
            delta = json.loads(line[6:])["choices"][0]["delta"]
            rebuilt += delta.get("content", "")
    assert rebuilt == "Hello, 世界! " * 10


def test_chat_completion_uses_recalled_memory():
    d = tempfile.mkdtemp(prefix="engram_oc_")
    try:
        svc = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        svc.remember("u", "I live in Shenzhen and work at Tencent.")
        seen = {}
        svc.llm = FakeLLM(handler=lambda p, s: seen.setdefault("system", s) and "" or "You live in Shenzhen.")
        body = {"model": "engram", "messages": [{"role": "user", "content": "where do I live?"}]}
        resp = oc.chat_completion(svc, "u", body)
        assert resp["object"] == "chat.completion"
        assert resp["choices"][0]["message"]["content"] == "You live in Shenzhen."
        assert resp["engram"]["recalled"] is True
        assert "Shenzhen" in (seen["system"] or "")  # the memory was injected into the system prompt
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_chat_completion_supports_as_of_memory_view():
    d = tempfile.mkdtemp(prefix="engram_oc_")
    try:
        svc = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        svc.import_("u", None, "records", [
            {"session_id": "old", "content": "Wei works at Tencent.", "event_time": 1_700_000_000.0},
            {"session_id": "new", "content": "Wei works at Moonshot AI.", "event_time": 1_702_592_000.0},
        ])
        seen = {}
        svc.llm = FakeLLM(handler=lambda p, s: seen.setdefault("system", s) and "" or "You worked at Tencent.")
        body = {"model": "engram", "messages": [{"role": "user", "content": "where did Wei work?"}]}
        resp = oc.chat_completion(
            svc,
            "u",
            body,
            as_of=1_700_864_000.0,
            known_at=2_000_000_000.0,
        )

        assert resp["engram"]["as_of"] == 1_700_864_000.0
        assert resp["engram"]["known_at"] == 2_000_000_000.0
        assert "Tencent" in (seen["system"] or "")
        assert "Moonshot AI" not in (seen["system"] or "")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_chat_completion_can_redact_sensitive_memory():
    d = tempfile.mkdtemp(prefix="engram_oc_")
    try:
        svc = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        svc.add_fact("u", "user", "has_disease", "diabetes")
        svc.add_fact("u", "user", "works_at", "Acme")
        seen = {}
        svc.llm = FakeLLM(handler=lambda p, s: seen.setdefault("system", s) and "" or "ok")
        body = {"model": "engram", "messages": [{"role": "user", "content": "what do you know about me?"}]}
        resp = oc.chat_completion(svc, "u", body, n_chunks=0, redact_sensitive=True)

        assert resp["engram"]["redacted_sensitive"] is True
        system = seen["system"] or ""
        assert "Acme" in system
        assert "diabetes" not in system.lower()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_chat_completion_uses_session_scoped_working_memory():
    d = tempfile.mkdtemp(prefix="engram_oc_")
    try:
        svc = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        svc.add_working("u", "today I am checking release notes", session_id="agent-thread-1")
        seen = {}
        svc.llm = FakeLLM(handler=lambda p, s: seen.setdefault("system", s) and "" or "ok")
        body = {"model": "engram", "messages": [{"role": "user", "content": "what am I doing?"}]}

        resp = oc.chat_completion(svc, "u", body, session_id="agent-thread-1")

        assert resp["engram"]["session_id"] == "agent-thread-1"
        assert "release notes" in (seen["system"] or "")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_chat_completion_without_llm_raises():
    d = tempfile.mkdtemp(prefix="engram_oc_")
    try:
        svc = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        with pytest.raises(oc.NoLLMConfigured):
            oc.chat_completion(svc, "u", {"messages": [{"role": "user", "content": "hi"}]})
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --- HTTP round-trip --------------------------------------------------------


@pytest.fixture()
def client_with_llm():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    d = tempfile.mkdtemp(prefix="engram_ocsrv_")
    os.environ.update(ENGRAM_DATA_DIR=d, ENGRAM_EMBEDDER="hashing", ENGRAM_OPEN="1")
    os.environ.pop("ENGRAM_LLM", None)
    os.environ.pop("ENGRAM_API_KEYS", None)
    from engram.server import app as appmod
    appmod._svc = None
    with TestClient(appmod.app) as c:
        appmod._svc.llm = FakeLLM(handler=lambda p, s: "You live in Shenzhen.")
        yield c, appmod
    shutil.rmtree(d, ignore_errors=True)


def test_chat_completions_endpoint(client_with_llm):
    c, appmod = client_with_llm
    h = {"Authorization": "Bearer chatuser"}
    c.post("/v1/remember", json={"content": "I live in Shenzhen."}, headers=h)
    r = c.post("/v1/chat/completions",
               json={"model": "engram", "messages": [{"role": "user", "content": "where do I live?"}]},
               headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "You live in Shenzhen."
    assert data["engram"]["recalled"] is True and data["engram"]["remembered"] is True
    # the FakeLLM saw the recalled memory injected into its system prompt
    assert any("Shenzhen" in (call["system"] or "") for call in appmod._svc.llm.calls)


def test_chat_completions_endpoint_supports_memory_as_of(client_with_llm):
    c, appmod = client_with_llm
    h = {"Authorization": "Bearer chat_asof"}
    c.post("/v1/import", json={
        "format": "records",
        "data": [
            {"session_id": "old", "content": "Wei works at Tencent.", "event_time": 1_700_000_000.0},
            {"session_id": "new", "content": "Wei works at Moonshot AI.", "event_time": 1_702_592_000.0},
        ],
    }, headers=h)

    r = c.post("/v1/chat/completions", json={
        "model": "engram",
        "messages": [{"role": "user", "content": "where did Wei work?"}],
        "memory": {
            "as_of": 1_700_864_000.0,
            "known_at": 2_000_000_000.0,
            "remember": False,
        },
    }, headers=h)

    assert r.status_code == 200
    data = r.json()
    assert data["engram"]["as_of"] == 1_700_864_000.0
    assert data["engram"]["known_at"] == 2_000_000_000.0
    assert data["engram"]["remembered"] is False
    system = appmod._svc.llm.calls[-1]["system"] or ""
    assert "Tencent" in system
    assert "Moonshot AI" not in system


def test_chat_completions_endpoint_supports_sensitive_redaction(client_with_llm):
    c, appmod = client_with_llm
    h = {"Authorization": "Bearer chat_redact"}
    c.post("/v1/facts", json={"predicate": "has_disease", "object": "diabetes"}, headers=h)
    c.post("/v1/facts", json={"predicate": "works_at", "object": "Acme"}, headers=h)

    r = c.post("/v1/chat/completions", json={
        "model": "engram",
        "messages": [{"role": "user", "content": "what do you know about me?"}],
        "memory": {"redact_sensitive": True, "n_chunks": 0, "remember": False},
    }, headers=h)

    assert r.status_code == 200
    data = r.json()
    assert data["engram"]["redacted_sensitive"] is True
    system = appmod._svc.llm.calls[-1]["system"] or ""
    assert "Acme" in system
    assert "diabetes" not in system.lower()


def test_chat_completions_endpoint_remembers_with_session_and_scope(client_with_llm):
    c, _ = client_with_llm
    h = {"Authorization": "Bearer chat_session"}

    r = c.post("/v1/chat/completions", json={
        "model": "engram",
        "messages": [{"role": "user", "content": "I work at Acme."}],
        "memory": {"session_id": "codex:repo:thread-1", "scope": "long"},
    }, headers=h)

    assert r.status_code == 200
    data = r.json()
    assert data["engram"]["session_id"] == "codex:repo:thread-1"
    assert data["engram"]["remembered"] is True
    assert data["engram"]["remember_scope"] == "long"

    memories = c.get("/v1/memories?include_sensitive=true", headers=h).json()
    assert any(e["session"] == "codex:repo:thread-1" for e in memories["episodes"])


def test_chat_completions_streaming(client_with_llm):
    c, _ = client_with_llm
    h = {"Authorization": "Bearer streamer"}
    r = c.post("/v1/chat/completions",
               json={"messages": [{"role": "user", "content": "hi"}], "stream": True}, headers=h)
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "[DONE]" in r.text and "chat.completion.chunk" in r.text


def test_chat_completions_503_without_llm(client_with_llm):
    c, appmod = client_with_llm
    appmod._svc.llm = None
    r = c.post("/v1/chat/completions",
               json={"messages": [{"role": "user", "content": "hi"}]}, headers={"Authorization": "Bearer x"})
    assert r.status_code == 503
