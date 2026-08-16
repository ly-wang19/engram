"""The Python SDK.

Tested against the real application rather than a mock: the injectable transport routes calls through
FastAPI's TestClient, so every assertion here is about the actual request the server receives and the
actual response it sends. A mocked SDK test only proves the SDK agrees with itself, which is exactly the
failure mode a client library has — drifting from the server it claims to speak to.
"""
from __future__ import annotations

import pytest

from engram.client import EngramClient, EngramError

pytest.importorskip("fastapi")


def _sdk(tmp_path, monkeypatch, api_key="tenant-a", **env):
    """An EngramClient wired to an in-process server, plus the app module for direct inspection."""
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
    http = TestClient(app_module.app)

    seen: list[dict] = []

    def transport(method, url, headers, body, timeout):
        path = url[len("http://testserver"):] if url.startswith("http://testserver") else url
        seen.append({"method": method, "path": path, "headers": headers})
        response = http.request(method, path, content=body, headers=headers)
        return response.status_code, dict(response.headers), response.content

    client = EngramClient(base_url="http://testserver", api_key=api_key, transport=transport)
    return client, app_module, seen


# --- round trips ---


def test_remember_then_recall(tmp_path, monkeypatch):
    client, _, _ = _sdk(tmp_path, monkeypatch)
    assert client.remember("Alice works at Acme Corp.")["ok"] is True
    assert "Acme" in client.recall("where does alice work")["context"]


def test_health_and_metrics_need_no_key(tmp_path, monkeypatch):
    client, _, _ = _sdk(tmp_path, monkeypatch, api_key=None)
    assert client.health()["service"] == "engram"
    assert set(client.metrics()) == {"uptime_s", "ops", "counts", "tokens"}


def test_profile_stats_and_memories(tmp_path, monkeypatch):
    client, _, _ = _sdk(tmp_path, monkeypatch)
    client.remember("Alice prefers oat milk.")
    assert isinstance(client.profile(), dict)
    assert isinstance(client.profile(structured=True), dict)
    assert isinstance(client.stats(), dict)
    assert isinstance(client.memories(limit=5), dict)


def test_sessions_and_working_memory(tmp_path, monkeypatch):
    client, _, _ = _sdk(tmp_path, monkeypatch)
    client.remember("A note.", session_id="s1")
    client.add_working("today my throat hurts", session_id="s1")
    assert isinstance(client.working_memory(session_id="s1"), dict)
    assert client.close_session("s1")
    assert isinstance(client.sessions(), dict)


def test_focus_and_policy_round_trip(tmp_path, monkeypatch):
    client, _, _ = _sdk(tmp_path, monkeypatch)
    client.set_focus(track=["cycling"], mute=[])
    assert "cycling" in str(client.get_focus())
    assert isinstance(client.get_policy(), dict)


def test_facts_and_export(tmp_path, monkeypatch):
    client, _, _ = _sdk(tmp_path, monkeypatch)
    client.add_fact("alice", "lives_in", "Shenzhen")
    assert isinstance(client.export(), dict)
    assert isinstance(client.conflicts(), dict)


# --- request shape ---


def test_bearer_key_is_sent(tmp_path, monkeypatch):
    client, _, seen = _sdk(tmp_path, monkeypatch, api_key="tenant-x")
    client.remember("hello")
    assert seen[-1]["headers"]["Authorization"] == "Bearer tenant-x"


def test_no_key_means_no_authorization_header(tmp_path, monkeypatch):
    client, _, seen = _sdk(tmp_path, monkeypatch, api_key=None)
    client.health()
    assert "Authorization" not in seen[-1]["headers"]


def test_query_params_skip_none_rather_than_sending_the_word_none(tmp_path, monkeypatch):
    """A None that reaches the URL becomes the literal string 'None' and the server filters on it."""
    client, _, seen = _sdk(tmp_path, monkeypatch)
    client.agent_status(session_id=None)
    assert "session_id" not in seen[-1]["path"]
    client.agent_status(session_id="s1")
    assert "session_id=s1" in seen[-1]["path"]


def test_path_ids_are_escaped(tmp_path, monkeypatch):
    """An id with a slash must not silently address a different route."""
    client, _, seen = _sdk(tmp_path, monkeypatch)
    with pytest.raises(EngramError):
        client.delete_fact("a/b")
    assert "a%2Fb" in seen[-1]["path"]


def test_idempotency_key_is_sent_and_honoured(tmp_path, monkeypatch):
    client, app_module, seen = _sdk(tmp_path, monkeypatch)
    first = client.remember("Alice visited Kyoto.", idempotency_key="retry-1")
    assert seen[-1]["headers"]["Idempotency-Key"] == "retry-1"
    second = client.remember("Alice visited Kyoto.", idempotency_key="retry-1")
    assert first == second

    episodes = app_module.svc().get("tenant-a").episodes_doc.values()
    assert len([ep for ep in episodes if "Kyoto" in ep.content]) == 1


def test_no_idempotency_header_when_unset(tmp_path, monkeypatch):
    client, _, seen = _sdk(tmp_path, monkeypatch)
    client.remember("no key")
    assert "Idempotency-Key" not in seen[-1]["headers"]


# --- errors ---


def test_non_2xx_raises_with_status_and_server_message(tmp_path, monkeypatch):
    client, _, _ = _sdk(tmp_path, monkeypatch)
    with pytest.raises(EngramError) as caught:
        client.forget(confirm=False)  # the server refuses without explicit confirmation
    assert caught.value.status == 400
    assert "confirm" in str(caught.value).lower()


def test_rate_limited_error_carries_retry_after(tmp_path, monkeypatch):
    """Retry-After lives in a header, so an SDK whose transport drops headers cannot report it — which
    is what makes a 429 unactionable for the caller."""
    client, _, _ = _sdk(tmp_path, monkeypatch, ENGRAM_RATE_LIMIT_PER_MIN="1")
    client.remember("one")
    with pytest.raises(EngramError) as caught:
        client.remember("two")
    assert caught.value.status == 429
    assert caught.value.retry_after is not None and caught.value.retry_after >= 1


def test_unreachable_server_raises_rather_than_hanging():
    """A connection failure is status 0 -- distinguishable from any answer the server could give."""
    client = EngramClient(base_url="http://127.0.0.1:1", api_key="k", timeout=1.0)
    with pytest.raises(EngramError) as caught:
        client.health()
    assert caught.value.status == 0


# --- admin ---


def test_admin_key_lifecycle_through_the_sdk(tmp_path, monkeypatch):
    client, app_module, _ = _sdk(tmp_path, monkeypatch, api_key="s3cret", ENGRAM_ADMIN_TOKEN="s3cret")

    issued = client.issue_key("alice", label="laptop")
    assert issued["key"].startswith("sk-engram-")
    assert any(rec["id"] == issued["id"] for rec in client.list_keys()["keys"])
    assert client.revoke_key(issued["id"])["revoked"] is True
    with pytest.raises(EngramError) as caught:
        client.revoke_key(issued["id"])
    assert caught.value.status == 404


def test_admin_surface_refuses_a_tenant_key(tmp_path, monkeypatch):
    """A tenant key must not be able to mint tenants."""
    client, _, _ = _sdk(tmp_path, monkeypatch, api_key="tenant-a", ENGRAM_ADMIN_TOKEN="s3cret")
    with pytest.raises(EngramError) as caught:
        client.issue_key("mallory")
    assert caught.value.status == 401
