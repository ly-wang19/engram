"""HTTP surface of the cross-instance migration path: POST an /v1/export payload straight back into
/v1/import on another namespace/instance and get memory, not a 500."""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client():
    d = tempfile.mkdtemp(prefix="engram_port_")
    os.environ.update(ENGRAM_DATA_DIR=d, ENGRAM_EMBEDDER="hashing", ENGRAM_OPEN="1")
    for var in ("ENGRAM_LLM", "ENGRAM_API_KEYS", "ENGRAM_ALLOW_ANONYMOUS", "ENGRAM_STORAGE"):
        os.environ.pop(var, None)
    from engram.server import app as appmod
    appmod._svc = None  # fresh singleton bound to the test env
    with TestClient(appmod.app) as c:
        yield c
    shutil.rmtree(d, ignore_errors=True)


def hdr(ns: str) -> dict:
    return {"Authorization": f"Bearer {ns}"}


def test_rest_export_import_roundtrip(client):
    src, dst = hdr("mover-src"), hdr("mover-dst")
    r = client.post("/v1/remember", json={"content": "I live in Lisbon and prefer tea."}, headers=src)
    assert r.status_code == 200

    exported = client.get("/v1/export", params={"include_sensitive": "true"}, headers=src).json()
    assert exported["engram_export_version"] == 1

    r = client.post("/v1/import", json={"data": exported, "format": "auto"}, headers=dst)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["format"] == "engram"

    out = client.get("/v1/export", params={"include_sensitive": "true"}, headers=dst).json()
    assert {f["id"] for f in out["facts"]} == {f["id"] for f in exported["facts"]}


def test_rest_import_bad_payload_is_400_not_500(client):
    r = client.post("/v1/import", json={"data": {"engram_export_version": 99}, "format": "engram"},
                    headers=hdr("mover-bad"))
    assert r.status_code == 400
    assert "version" in r.json()["detail"]


def test_rest_import_malformed_records_is_400_not_500(client):
    # a dict that is neither a known chat shape nor a valid export used to escape as a raw 500
    r = client.post("/v1/import", json={"data": {"whatever": 1}, "format": "records"},
                    headers=hdr("mover-bad"))
    assert r.status_code == 400
