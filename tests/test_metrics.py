"""Live service metrics (CLAUDE.md Bet D online). What matters: the SLO-bearing paths are timed
(p50/p95), the token-savings ratio is derived from real recalls, the async backlog is visible, and the
payload is aggregate-only — no namespace names, no user content. All offline."""
from __future__ import annotations

import json
import shutil
import tempfile

import pytest

from engram.metrics import Metrics
from engram.service import MemoryService

_SECRET_NS = "alice-secret-ns"
_SECRET_TEXT = "I live in Shenzhen"


@pytest.fixture()
def svc():
    d = tempfile.mkdtemp(prefix="engram_metrics_")
    try:
        yield MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --- the Metrics primitive ----------------------------------------------------


def test_percentiles_and_counts():
    m = Metrics()
    for ms in (10, 20, 30, 40, 100):
        m.observe("recall", ms / 1000)
    snap = m.snapshot()
    op = snap["ops"]["recall"]
    assert op["n"] == 5 and op["window"] == 5
    assert op["p50_ms"] == 30.0
    assert op["p95_ms"] == 100.0  # nearest-rank on a 5-sample window -> the max
    assert op["max_ms"] == 100.0


def test_savings_ratio_needs_both_sides():
    m = Metrics()
    m.tokens(100)  # context only -> no ratio yet
    assert m.snapshot()["tokens"]["savings_ratio"] is None
    m.tokens(100, full=1600)
    snap = m.snapshot()["tokens"]
    assert snap["recalls_with_baseline"] == 1
    assert snap["savings_ratio"] == 8.0  # 1600 / (100 + 100)


# --- service wiring -------------------------------------------------------------


def test_service_paths_are_timed_and_tokens_recorded(svc):
    svc.remember(_SECRET_NS, f"My name is Wei and {_SECRET_TEXT}.")
    svc.recall(_SECRET_NS, "where do I live")                 # context tokens only
    svc.recall(_SECRET_NS, "where do I live", answer=True)    # + full baseline (no LLM -> answer "")
    snap = svc.metrics_snapshot()

    assert snap["ops"]["remember"]["n"] == 1
    assert snap["ops"]["recall"]["n"] == 2
    assert snap["ops"]["recall"]["p95_ms"] >= snap["ops"]["recall"]["p50_ms"] >= 0
    t = snap["tokens"]
    assert t["context_total"] > 0 and t["full_total"] > 0 and t["recalls_with_baseline"] == 1
    assert snap["async"] == {"enabled": False, "queue_depth": 0, "pending_users": 0}
    assert snap["users_hot"] == 1


def test_payload_is_aggregate_only(svc):
    svc.remember(_SECRET_NS, f"My name is Wei and {_SECRET_TEXT}.")
    svc.recall(_SECRET_NS, "where do I live", answer=True)
    raw = json.dumps(svc.metrics_snapshot(), ensure_ascii=False)
    assert _SECRET_NS not in raw      # no namespace names
    assert "Shenzhen" not in raw      # no user content
    assert "where do I live" not in raw  # no query text


def test_async_backlog_is_visible(monkeypatch):
    monkeypatch.setenv("ENGRAM_ASYNC_CONSOLIDATION", "1")
    d = tempfile.mkdtemp(prefix="engram_metrics_async_")
    try:
        s = MemoryService(data_dir=d, embedder_name="hashing", llm_name="")
        s.remember("u", "My name is Wei and I live in Shenzhen.")
        assert s.metrics_snapshot()["async"]["enabled"] is True
        s.flush()
        snap = s.metrics_snapshot()["async"]
        assert snap["queue_depth"] == 0 and snap["pending_users"] == 0  # drained
        s.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_http_metrics_endpoint(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    d = tempfile.mkdtemp(prefix="engram_metrics_http_")
    monkeypatch.setenv("ENGRAM_DATA_DIR", d)
    monkeypatch.setenv("ENGRAM_EMBEDDER", "hashing")
    monkeypatch.setenv("ENGRAM_OPEN", "1")
    for var in ("ENGRAM_API_KEYS", "ENGRAM_SPACES", "ENGRAM_LLM"):
        monkeypatch.delenv(var, raising=False)
    from engram.server import app as appmod
    appmod._svc = None
    try:
        with TestClient(appmod.app) as c:
            h = {"Authorization": "Bearer metricsuser"}
            c.post("/v1/remember", json={"content": "I live in Shenzhen."}, headers=h)
            c.post("/v1/recall", json={"query": "where do I live"}, headers=h)
            r = c.get("/metrics")  # open like /health
            assert r.status_code == 200
            body = r.json()
            assert body["ops"]["remember"]["n"] >= 1 and body["ops"]["recall"]["n"] >= 1
            assert body["tokens"]["recalls_with_baseline"] >= 1  # HTTP recall computes the baseline
            assert "metricsuser" not in r.text
    finally:
        appmod._svc = None
        shutil.rmtree(d, ignore_errors=True)
