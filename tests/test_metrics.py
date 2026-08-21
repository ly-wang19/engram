"""Live service metrics.

Two properties carry the weight here. The savings ratio must be computed from calls that measured both
sides -- mixing a full-history total over a context total drawn from a larger set of calls understates the
saving, which is the kind of error that quietly makes a headline number look worse than it is. And the
payload must stay aggregate-only, because the endpoint is unauthenticated: a namespace name leaking into
it would tell any caller which tenants exist.
"""
from __future__ import annotations

import json

import pytest

from engram.metrics import Metrics, timed


def test_latency_percentiles_and_counts():
    m = Metrics()
    for seconds in (0.001, 0.002, 0.003, 0.010):
        m.observe("recall", seconds)
    snap = m.snapshot()["ops"]["recall"]
    assert snap["n"] == 4
    assert snap["window"] == 4
    assert snap["max_ms"] == 10.0
    assert snap["p50_ms"] <= snap["p95_ms"] <= snap["max_ms"]


def test_window_is_bounded_so_memory_cannot_grow():
    """Percentiles must describe current behaviour, and the process must not accumulate samples forever."""
    m = Metrics(window=8)
    for i in range(1000):
        m.observe("remember", i / 1000)
    snap = m.snapshot()["ops"]["remember"]
    assert snap["window"] == 8
    assert snap["n"] == 1000, "the count is monotonic even though the sample is windowed"


def test_savings_ratio_uses_only_calls_that_measured_both_sides():
    """The regression this guards.

    Nine cheap recalls skip the baseline; one measures both. Dividing the full-history total by *every*
    context served would report roughly 1x -- as if memory saved nothing -- when the one comparable call
    shows 10x.
    """
    m = Metrics()
    for _ in range(9):
        m.tokens(100)  # no baseline computed on this path
    m.tokens(100, 1000)

    tokens = m.snapshot()["tokens"]
    assert tokens["context_total"] == 1000, "total volume still counts every served context"
    assert tokens["calls_with_baseline"] == 1
    assert tokens["savings_ratio"] == 10.0


def test_savings_ratio_is_absent_until_measured():
    """No pairs means no ratio. A fabricated number would be worse than none."""
    m = Metrics()
    assert m.snapshot()["tokens"]["savings_ratio"] is None
    m.tokens(100)
    assert m.snapshot()["tokens"]["savings_ratio"] is None


def test_counters_are_separate_from_timed_operations():
    m = Metrics()
    m.observe("remember", 0.001)
    m.count("remember_degraded")
    snap = m.snapshot()
    assert "remember" in snap["ops"]
    assert snap["counts"]["remember_degraded"] == 1
    assert "remember" not in snap["counts"], "a timed op should not be duplicated as a bare counter"


def test_snapshot_is_json_serialisable():
    m = Metrics()
    m.observe("recall", 0.001)
    m.tokens(10, 100)
    json.dumps(m.snapshot())  # the endpoint returns this directly


def test_timed_records_even_when_the_call_raises():
    """A failing operation is exactly the one whose latency matters."""

    class Svc:
        def __init__(self):
            self.metrics = Metrics()

        @timed("boom")
        def boom(self):
            raise ValueError("nope")

    svc = Svc()
    with pytest.raises(ValueError):
        svc.boom()
    assert svc.metrics.snapshot()["ops"]["boom"]["n"] == 1


def test_timed_is_a_noop_without_metrics():
    """Objects constructed without a metrics attribute must still work."""

    class Bare:
        @timed("op")
        def run(self):
            return 42

    assert Bare().run() == 42


# --- wiring ---


def _service(tmp_path):
    from engram.service import MemoryService

    return MemoryService(data_dir=str(tmp_path))


def test_service_records_remember_and_recall(tmp_path):
    svc = _service(tmp_path)
    svc.remember("alice", "Alice works at Acme Corp.")
    svc.recall("alice", "where does alice work")

    ops = svc.metrics.snapshot()["ops"]
    assert ops["remember"]["n"] == 1
    assert ops["recall"]["n"] == 1


def test_metrics_payload_leaks_no_tenant_identity(tmp_path):
    """The endpoint is unauthenticated, so this is a privacy boundary, not a nicety."""
    svc = _service(tmp_path)
    secret_user = "acme-industries-prod"
    svc.remember(secret_user, "The launch date is March 3rd.")
    svc.recall(secret_user, "when is the launch")

    payload = json.dumps(svc.metrics.snapshot())
    assert secret_user not in payload
    assert "launch" not in payload
    assert "March" not in payload


def test_metrics_endpoint_is_open_and_aggregate(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    del fastapi
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ENGRAM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_OPEN", "1")
    import engram.server.app as app_module

    app_module._svc = None  # force a rebuild against the temp data dir
    client = TestClient(app_module.app)
    assert app_module.svc().data_dir == str(tmp_path), "test must exercise its own service, not a leftover"

    response = client.get("/metrics")  # no Authorization header
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"uptime_s", "ops", "counts", "tokens"}

    # And it reflects real traffic through the app, not just an empty shell.
    client.post("/v1/remember", json={"content": "hi"}, headers={"Authorization": "Bearer tenant-x"})
    after = client.get("/metrics").json()
    assert after["ops"]["remember"]["n"] >= 1
    assert "tenant-x" not in json.dumps(after)


# --- defence counters ---
#
# The rate limiter, the idempotency cache and key resolution are otherwise black boxes: an operator
# cannot tell whether they are doing anything. These counters are aggregate on purpose — /metrics is
# unauthenticated, so a per-tenant breakdown would reveal which tenants exist.


def _guarded_client(tmp_path, monkeypatch, **env):
    fastapi = pytest.importorskip("fastapi")
    del fastapi
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ENGRAM_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ENGRAM_API_KEYS", raising=False)
    monkeypatch.delenv("ENGRAM_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("ENGRAM_OPEN", "1")
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    import engram.server.app as app_module

    app_module._svc = None
    app_module._keystore = None
    app_module._keystore_path = None
    app_module._limiter = None
    app_module._idempotency = None
    return TestClient(app_module.app), app_module


def test_rate_limited_requests_are_counted(tmp_path, monkeypatch):
    client, _ = _guarded_client(tmp_path, monkeypatch, ENGRAM_RATE_LIMIT_PER_MIN="1")
    headers = {"Authorization": "Bearer tenant-a"}
    client.post("/v1/remember", json={"content": "one"}, headers=headers)
    assert client.post("/v1/remember", json={"content": "two"}, headers=headers).status_code == 429

    assert client.get("/metrics").json()["counts"]["rate_limited"] == 1


def test_idempotent_replays_are_counted(tmp_path, monkeypatch):
    client, _ = _guarded_client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer tenant-a", "Idempotency-Key": "retry-1"}
    body = {"content": "Alice visited Kyoto."}
    client.post("/v1/remember", json=body, headers=headers)
    counts = client.get("/metrics").json()["counts"]
    assert "idempotent_replays" not in counts, "the first call is work, not a replay"

    client.post("/v1/remember", json=body, headers=headers)
    assert client.get("/metrics").json()["counts"]["idempotent_replays"] == 1


def test_rejected_auth_is_counted(tmp_path, monkeypatch):
    client, _ = _guarded_client(tmp_path, monkeypatch, ENGRAM_API_KEYS="carol:sk-carol")
    monkeypatch.delenv("ENGRAM_OPEN", raising=False)
    assert client.post(
        "/v1/remember", json={"content": "hi"}, headers={"Authorization": "Bearer wrong-key"}
    ).status_code == 401
    assert client.get("/metrics").json()["counts"]["auth_rejected"] == 1


def test_defence_counters_never_name_a_tenant(tmp_path, monkeypatch):
    """The privacy boundary: /metrics is open, so counting must not enumerate tenants."""
    client, _ = _guarded_client(tmp_path, monkeypatch, ENGRAM_RATE_LIMIT_PER_MIN="1")
    secret_tenant = "acme-industries-prod"
    headers = {"Authorization": f"Bearer {secret_tenant}", "Idempotency-Key": "k"}
    client.post("/v1/remember", json={"content": "hi"}, headers=headers)
    client.post("/v1/remember", json={"content": "hi"}, headers=headers)

    payload = client.get("/metrics").text
    assert secret_tenant not in payload
