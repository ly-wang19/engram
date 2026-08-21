"""Runtime-issued API keys.

This is an authentication surface, so the tests that matter are the ones about refusing, not the ones
about admitting: the admin surface must be absent unless deliberately enabled, a revoked key must stop
working, an unreadable key store must fail closed rather than open, and one tenant's key must never
resolve to another's namespace.

One behaviour is specifically a data-loss guard. A corrupt key file must make the store refuse to load,
because starting empty would reject every issued key and then the first `issue()` would rewrite the file
and destroy the records that were only unreadable.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from engram.server.keys import KEY_PREFIX, KeyStore, KeyStoreError

# --- store ---


def test_issued_key_resolves_to_its_tenant(tmp_path):
    store = KeyStore(str(tmp_path / "keys.json"))
    issued = store.issue("alice", label="laptop")
    assert issued["key"].startswith(KEY_PREFIX)
    assert store.resolve(issued["key"]) == "alice"


def test_secret_is_never_persisted(tmp_path):
    """A leaked key file must not be replayable as credentials."""
    path = tmp_path / "keys.json"
    store = KeyStore(str(path))
    issued = store.issue("alice")
    on_disk = path.read_text(encoding="utf-8")
    assert issued["key"] not in on_disk
    assert "hash" in json.loads(on_disk)["keys"][0]


def test_listing_exposes_neither_secret_nor_digest(tmp_path):
    """Publishing the digest would let anyone verify a guessed key offline."""
    store = KeyStore(str(tmp_path / "keys.json"))
    issued = store.issue("alice")
    listed = store.list()
    assert len(listed) == 1
    assert "hash" not in listed[0]
    assert "key" not in listed[0]
    assert listed[0]["id"] == issued["id"]


def test_revoked_key_stops_working(tmp_path):
    store = KeyStore(str(tmp_path / "keys.json"))
    issued = store.issue("alice")
    assert store.revoke(issued["id"]) is True
    assert store.resolve(issued["key"]) is None
    assert store.revoke(issued["id"]) is False, "revoking twice is not a second success"


def test_revocation_survives_a_restart(tmp_path):
    path = str(tmp_path / "keys.json")
    store = KeyStore(path)
    issued = store.issue("alice")
    store.revoke(issued["id"])
    assert KeyStore(path).resolve(issued["key"]) is None


def test_keys_survive_a_restart(tmp_path):
    path = str(tmp_path / "keys.json")
    issued = KeyStore(path).issue("alice")
    assert KeyStore(path).resolve(issued["key"]) == "alice"


def test_one_tenants_key_never_resolves_to_another(tmp_path):
    store = KeyStore(str(tmp_path / "keys.json"))
    alice = store.issue("alice")
    bob = store.issue("bob")
    assert store.resolve(alice["key"]) == "alice"
    assert store.resolve(bob["key"]) == "bob"
    assert store.list("alice") == [rec for rec in store.list() if rec["user"] == "alice"]


def test_unknown_and_empty_tokens_resolve_to_nothing(tmp_path):
    store = KeyStore(str(tmp_path / "keys.json"))
    store.issue("alice")
    assert store.resolve("") is None
    assert store.resolve("sk-engram-not-a-real-key") is None


def test_corrupt_store_refuses_to_load_rather_than_overwrite(tmp_path):
    """The data-loss guard. Starting empty would reject every issued key, and the next issue() would
    rewrite the file over records that were merely unreadable."""
    path = tmp_path / "keys.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(KeyStoreError):
        KeyStore(str(path))
    assert path.read_text(encoding="utf-8") == "{not json", "the unreadable file must be left intact"


def test_malformed_records_are_rejected(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"keys": [{"id": "key_1"}]}), encoding="utf-8")
    with pytest.raises(KeyStoreError):
        KeyStore(str(path))


def test_key_file_is_owner_only(tmp_path):
    """It holds no secrets, but it does enumerate the tenants on this deployment."""
    path = tmp_path / "keys.json"
    KeyStore(str(path)).issue("alice")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


def test_a_key_must_belong_to_a_tenant(tmp_path):
    store = KeyStore(str(tmp_path / "keys.json"))
    with pytest.raises(ValueError):
        store.issue("   ")


# --- HTTP surface ---


def _client(tmp_path, monkeypatch, **env):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ENGRAM_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ENGRAM_API_KEYS", raising=False)
    monkeypatch.delenv("ENGRAM_OPEN", raising=False)
    monkeypatch.delenv("ENGRAM_ADMIN_TOKEN", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    import engram.server.app as app_module

    app_module._svc = None
    app_module._keystore = None
    app_module._keystore_path = None
    app_module._limiter = None
    app_module._idempotency = None
    return TestClient(app_module.app), app_module


def test_admin_surface_is_absent_unless_enabled(tmp_path, monkeypatch):
    """Fail closed: an open deployment must not let a passer-by mint tenants."""
    client, _ = _client(tmp_path, monkeypatch, ENGRAM_OPEN="1")
    response = client.post("/v1/admin/keys", json={"user": "mallory"})
    assert response.status_code == 403


def test_wrong_admin_token_is_rejected(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, ENGRAM_ADMIN_TOKEN="s3cret")
    assert client.post(
        "/v1/admin/keys", json={"user": "alice"}, headers={"Authorization": "Bearer wrong"}
    ).status_code == 401
    assert client.post("/v1/admin/keys", json={"user": "alice"}).status_code == 401


def test_issued_key_authenticates_a_real_request(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, ENGRAM_ADMIN_TOKEN="s3cret")
    admin = {"Authorization": "Bearer s3cret"}

    issued = client.post("/v1/admin/keys", json={"user": "alice"}, headers=admin).json()
    assert client.post(
        "/v1/remember", json={"content": "Alice works at Acme."},
        headers={"Authorization": f"Bearer {issued['key']}"},
    ).status_code == 200


def test_revoked_key_is_rejected_by_the_api(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, ENGRAM_ADMIN_TOKEN="s3cret")
    admin = {"Authorization": "Bearer s3cret"}
    issued = client.post("/v1/admin/keys", json={"user": "alice"}, headers=admin).json()
    caller = {"Authorization": f"Bearer {issued['key']}"}

    assert client.post("/v1/remember", json={"content": "one"}, headers=caller).status_code == 200
    assert client.delete(f"/v1/admin/keys/{issued['id']}", headers=admin).status_code == 200
    assert client.post("/v1/remember", json={"content": "two"}, headers=caller).status_code == 401
    assert client.delete(f"/v1/admin/keys/{issued['id']}", headers=admin).status_code == 404


def test_issued_keys_isolate_namespaces(tmp_path, monkeypatch):
    """The isolation the whole multi-tenant model rests on — checked through the API with bob's own
    key, not by inspecting the service, so it would fail if bob's key resolved to alice's namespace."""
    client, _ = _client(tmp_path, monkeypatch, ENGRAM_ADMIN_TOKEN="s3cret")
    admin = {"Authorization": "Bearer s3cret"}
    alice = client.post("/v1/admin/keys", json={"user": "alice"}, headers=admin).json()
    bob = client.post("/v1/admin/keys", json={"user": "bob"}, headers=admin).json()

    client.post(
        "/v1/remember", json={"content": "Alice's private note about the merger."},
        headers={"Authorization": f"Bearer {alice['key']}"},
    )

    as_bob = {"Authorization": f"Bearer {bob['key']}"}
    recalled = client.post("/v1/recall", json={"query": "merger"}, headers=as_bob)
    assert recalled.status_code == 200
    assert "merger" not in recalled.json().get("context", "")

    listed = client.get("/v1/memories", headers=as_bob)
    assert "Alice's private note" not in listed.text


def test_static_env_keys_still_work_alongside_issued_ones(tmp_path, monkeypatch):
    """Existing deployments must not break when the self-serve path appears."""
    client, _ = _client(
        tmp_path, monkeypatch, ENGRAM_ADMIN_TOKEN="s3cret", ENGRAM_API_KEYS="carol:sk-carol"
    )
    assert client.post(
        "/v1/remember", json={"content": "hi"}, headers={"Authorization": "Bearer sk-carol"}
    ).status_code == 200

    issued = client.post(
        "/v1/admin/keys", json={"user": "alice"}, headers={"Authorization": "Bearer s3cret"}
    ).json()
    assert client.post(
        "/v1/remember", json={"content": "hi"},
        headers={"Authorization": f"Bearer {issued['key']}"},
    ).status_code == 200


def test_unreadable_key_store_fails_closed(tmp_path, monkeypatch):
    """A broken store must reject requests, never wave them through."""
    client, app_module = _client(tmp_path, monkeypatch, ENGRAM_API_KEYS="carol:sk-carol")
    app_module.svc()  # ensure the data dir exists before corrupting the file inside it
    with open(os.path.join(str(tmp_path), "api_keys.json"), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    app_module._keystore = None
    app_module._keystore_path = None

    response = client.post(
        "/v1/remember", json={"content": "hi"}, headers={"Authorization": "Bearer sk-carol"}
    )
    assert response.status_code == 503, "an unreadable key store must not fall through to other auth"


def test_listing_over_http_hides_secrets(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, ENGRAM_ADMIN_TOKEN="s3cret")
    admin = {"Authorization": "Bearer s3cret"}
    issued = client.post("/v1/admin/keys", json={"user": "alice"}, headers=admin).json()

    body = client.get("/v1/admin/keys", headers=admin).text
    assert issued["key"] not in body
    assert "hash" not in json.loads(body)["keys"][0]
