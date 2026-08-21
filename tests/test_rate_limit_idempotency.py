"""Per-tenant rate limiting and Idempotency-Key replay.

Both defend the multi-tenant surface against a different failure. The limiter stops one caller spending
the whole process; idempotency stops a client's timeout-and-retry storing the same episode twice and
paying to consolidate it twice, because the first request did succeed and only its response was lost.

Two properties are load-bearing and easy to get subtly wrong, so they are tested directly: a rejected
request must not extend its own window (or a retrying client never recovers), and a cached response must
never cross tenants.
"""
from __future__ import annotations

import pytest

from engram.server.limits import IdempotencyCache, RateLimiter

# --- limiter ---


def test_requests_are_allowed_up_to_the_limit_then_rejected():
    limiter = RateLimiter(per_min=3)
    assert [limiter.check("u1", now=100.0)[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = limiter.check("u1", now=100.0)
    assert allowed is False
    assert 0 < retry_after <= 60


def test_a_rejected_request_does_not_extend_the_window():
    """The trap: counting rejections keeps a retrying client's window permanently full."""
    limiter = RateLimiter(per_min=2)
    limiter.check("u1", now=0.0)
    limiter.check("u1", now=0.0)
    for _ in range(10):  # a client hammering while blocked
        assert limiter.check("u1", now=30.0)[0] is False
    assert limiter.check("u1", now=61.0)[0] is True, "the window must clear once the originals age out"


def test_window_slides():
    limiter = RateLimiter(per_min=2)
    limiter.check("u1", now=0.0)
    limiter.check("u1", now=59.0)
    assert limiter.check("u1", now=59.5)[0] is False
    assert limiter.check("u1", now=60.5)[0] is True, "the first hit has aged out"


def test_tenants_have_independent_budgets():
    limiter = RateLimiter(per_min=1)
    assert limiter.check("u1", now=0.0)[0] is True
    assert limiter.check("u1", now=0.0)[0] is False
    assert limiter.check("u2", now=0.0)[0] is True, "one tenant must not consume another's budget"


def test_disabled_limiter_allows_everything():
    limiter = RateLimiter(per_min=0)
    assert limiter.enabled is False
    assert all(limiter.check("u1", now=0.0)[0] for _ in range(100))


def test_pruning_stops_the_tenant_map_growing_without_bound():
    """Every tenant that ever called would otherwise stay in the map forever -- a slow leak that only
    bites the deployment with the most tenants."""
    limiter = RateLimiter(per_min=5)
    for i in range(50):
        limiter.check(f"u{i}", now=0.0)
    assert limiter.tracked_tenants == 50

    limiter.check("recent", now=100.0)
    assert limiter.prune(now=100.0) == 50, "the aged-out tenants should be swept"
    assert limiter.tracked_tenants == 1, "the tenant still inside the window must survive"


def test_retry_after_shrinks_as_the_window_drains():
    limiter = RateLimiter(per_min=1)
    limiter.check("u1", now=0.0)
    _, early = limiter.check("u1", now=10.0)
    _, late = limiter.check("u1", now=50.0)
    assert early > late > 0


# --- idempotency ---


def test_replays_the_first_response_for_a_repeated_key():
    cache = IdempotencyCache()
    cache.put("u1", "k1", {"ok": True, "id": "ep_1"}, now=0.0)
    assert cache.get("u1", "k1", now=1.0) == {"ok": True, "id": "ep_1"}


def test_cached_responses_never_cross_tenants():
    """Two namespaces picking the same key must not read each other's response."""
    cache = IdempotencyCache()
    cache.put("alice", "same-key", {"owner": "alice"}, now=0.0)
    assert cache.get("bob", "same-key", now=0.0) is None


def test_entries_expire():
    cache = IdempotencyCache(ttl_seconds=10.0)
    cache.put("u1", "k1", {"ok": True}, now=0.0)
    assert cache.get("u1", "k1", now=9.0) is not None
    assert cache.get("u1", "k1", now=11.0) is None


def test_missing_key_is_never_cached():
    cache = IdempotencyCache()
    cache.put("u1", "", {"ok": True}, now=0.0)
    assert len(cache) == 0
    assert cache.get("u1", "", now=0.0) is None


def test_oldest_entries_are_evicted_at_capacity():
    cache = IdempotencyCache(max_entries=3)
    for i in range(5):
        cache.put("u1", f"k{i}", {"n": i}, now=float(i))
    assert len(cache) == 3
    assert cache.get("u1", "k0", now=5.0) is None
    assert cache.get("u1", "k4", now=5.0) == {"n": 4}


# --- wiring ---


def _client(tmp_path, monkeypatch, **env):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ENGRAM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_OPEN", "1")
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    import engram.server.app as app_module

    app_module._svc = None  # rebuild the service against the temp data dir
    app_module._limiter = None  # and the limiter against this test's configured limit
    app_module._idempotency = None
    return TestClient(app_module.app), app_module


def test_endpoint_returns_429_with_retry_after(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, ENGRAM_RATE_LIMIT_PER_MIN="2")
    headers = {"Authorization": "Bearer tenant-a"}

    assert client.post("/v1/remember", json={"content": "one"}, headers=headers).status_code == 200
    assert client.post("/v1/remember", json={"content": "two"}, headers=headers).status_code == 200
    blocked = client.post("/v1/remember", json={"content": "three"}, headers=headers)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1, "a 429 without Retry-After leaves clients guessing"


def test_one_tenant_cannot_exhaust_anothers_budget(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, ENGRAM_RATE_LIMIT_PER_MIN="1")
    assert client.post(
        "/v1/remember", json={"content": "a"}, headers={"Authorization": "Bearer tenant-a"}
    ).status_code == 200
    assert client.post(
        "/v1/remember", json={"content": "a again"}, headers={"Authorization": "Bearer tenant-a"}
    ).status_code == 429
    assert client.post(
        "/v1/remember", json={"content": "b"}, headers={"Authorization": "Bearer tenant-b"}
    ).status_code == 200


def test_rate_limiting_is_off_by_default(tmp_path, monkeypatch):
    """Existing deployments and the zero-setup demo must be unaffected."""
    client, _ = _client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer tenant-a"}
    for i in range(12):
        assert client.post("/v1/remember", json={"content": f"m{i}"}, headers=headers).status_code == 200


def test_health_stays_reachable_while_a_tenant_is_limited(tmp_path, monkeypatch):
    """Probes must not be rate limited, or a busy tenant takes the deployment down with it."""
    client, _ = _client(tmp_path, monkeypatch, ENGRAM_RATE_LIMIT_PER_MIN="1")
    headers = {"Authorization": "Bearer tenant-a"}
    client.post("/v1/remember", json={"content": "one"}, headers=headers)
    assert client.post("/v1/remember", json={"content": "two"}, headers=headers).status_code == 429
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200


def test_retried_remember_stores_once(tmp_path, monkeypatch):
    """The point of the header: the same call twice must leave one episode, not two."""
    client, app_module = _client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer tenant-a", "Idempotency-Key": "retry-1"}
    body = {"content": "Alice works at Acme Corp."}

    first = client.post("/v1/remember", json=body, headers=headers)
    second = client.post("/v1/remember", json=body, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json(), "a retry must replay the first response verbatim"

    episodes = app_module.svc().get("tenant-a").episodes_doc.values()
    assert len([ep for ep in episodes if "Acme" in ep.content]) == 1


def test_without_the_header_a_repeat_is_a_new_write(tmp_path, monkeypatch):
    """Idempotency is opt-in; two deliberate identical writes must both land."""
    client, app_module = _client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer tenant-a"}
    body = {"content": "Alice visited Kyoto."}
    client.post("/v1/remember", json=body, headers=headers)
    client.post("/v1/remember", json=body, headers=headers)

    episodes = app_module.svc().get("tenant-a").episodes_doc.values()
    assert len([ep for ep in episodes if "Kyoto" in ep.content]) == 2


def test_idempotency_key_is_scoped_to_the_tenant(tmp_path, monkeypatch):
    """Otherwise one tenant's reply could be served to another — a cross-tenant data leak."""
    client, app_module = _client(tmp_path, monkeypatch)
    key = {"Idempotency-Key": "shared"}
    client.post(
        "/v1/remember", json={"content": "alice secret"},
        headers={"Authorization": "Bearer alice", **key},
    )
    client.post(
        "/v1/remember", json={"content": "bob secret"},
        headers={"Authorization": "Bearer bob", **key},
    )

    bob_episodes = [ep.content for ep in app_module.svc().get("bob").episodes_doc.values()]
    assert any("bob secret" in text for text in bob_episodes)
    assert not any("alice secret" in text for text in bob_episodes)
