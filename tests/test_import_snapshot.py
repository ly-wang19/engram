"""The export -> import portability round-trip (an Engram snapshot restores, it is not re-ingested):
facts keep bi-temporal stamps + supersession chains, episodes re-enter consolidated, the whole thing
is idempotent, and /v1/import turns parse failures into 400s instead of 500s. Offline (hashing
embedder + rule extractor), like every other test."""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

from engram.memory import Memory


def _mk_memory() -> Memory:
    return Memory()


class TestSnapshotRoundtrip:
    def _service(self, tmpdir: str):
        from engram.service import MemoryService
        return MemoryService(data_dir=tmpdir, embedder_name="hashing", llm_name="")

    def test_full_roundtrip_restores_facts_episodes_and_chains(self):
        d = tempfile.mkdtemp(prefix="engram_snap_")
        try:
            svc = self._service(d)
            # seed: one supersession chain (Beijing -> Shanghai) + one manual authoritative fact
            svc.remember("alice", "I live in Beijing.")
            svc.remember("alice", "I moved, I now live in Shanghai.")
            svc.add_fact("alice", "alice", "favorite_editor", "vim")
            snap = svc.export("alice", include_sensitive=True)
            assert snap["engram_export_version"] == 1 and snap["episodes"], "need a full export"

            out = svc.import_(user="bob", data=snap, format="auto")
            assert out["ok"] is True and out["format"] == "engram"
            assert out["facts_restored"] >= 3  # live x2 (Shanghai, vim) + superseded Beijing
            assert out["episodes_restored"] == len(snap["episodes"])

            mem = svc.get("bob")
            facts = [f for f in mem._all_facts() if f.user_id == "bob"]
            by_obj = {f.object: f for f in facts}
            # bi-temporal survival: the superseded row is still invalid, the live row still live
            assert "Beijing" in by_obj and by_obj["Beijing"].invalid_at is not None
            assert "Shanghai" in by_obj and by_obj["Shanghai"].is_live()
            # the supersession chain re-links to the NEW id of the Beijing fact (no dangling foreign id)
            assert by_obj["Shanghai"].supersedes == by_obj["Beijing"].id
            # a manually asserted fact keeps its authoritative provenance class
            assert by_obj["vim"].source == "user"
            # restored episodes must never re-enter System-2 (their facts came with the snapshot)
            eps = [ep for ep in mem.episodes_doc.values() if ep.user_id == "bob"]
            assert eps and all(ep.consolidated for ep in eps)
            # and the memory actually answers from the restored namespace
            ctx = svc.recall("bob", "where do I live?")["context"]
            assert "Shanghai" in ctx
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_reimport_is_idempotent(self):
        d = tempfile.mkdtemp(prefix="engram_snap_")
        try:
            svc = self._service(d)
            svc.remember("alice", "My favorite drink is oolong tea.")
            snap = svc.export("alice", include_sensitive=True)
            first = svc.import_(user="bob", data=snap)
            again = svc.import_(user="bob", data=snap)
            assert first["facts_restored"] >= 1
            assert again["facts_restored"] == 0 and again["episodes_restored"] == 0
            assert again["facts_skipped"] == first["facts_restored"]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_safe_export_restores_facts_without_episodes(self):
        # The default share-safe export has no episodes; restoring facts alone must still work.
        d = tempfile.mkdtemp(prefix="engram_snap_")
        try:
            svc = self._service(d)
            svc.remember("alice", "I work at Acme Corp.")
            snap = svc.export("alice")  # include_sensitive=False
            assert snap["episodes"] == []
            out = svc.import_(user="carol", data=snap)
            assert out["ok"] is True and out["facts_restored"] >= 1
            assert "Acme" in svc.recall("carol", "where do I work?")["context"]
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestSnifferAndMemoryLevel:
    def test_sniff_recognizes_snapshot_and_parse_gives_actionable_error(self):
        from engram.connectors import parse, sniff
        snap = {"engram_export_version": 1, "facts": [], "episodes": []}
        assert sniff(snap) == "engram"
        with pytest.raises(ValueError, match="import_snapshot"):
            parse(snap)

    def test_memory_import_snapshot_direct(self):
        src = _mk_memory()
        src.add("I love hiking in the Alps.", user_id="u1")
        src.consolidate()
        # hand-build the snapshot rows the way service.export does (subject/predicate/object + stamps)
        facts = [{
            "id": f.id, "subject": f.subject, "predicate": f.predicate, "object": f.object,
            "text": f.text, "source": f.source, "valid_at": f.valid_at, "invalid_at": f.invalid_at,
            "created_at": f.created_at, "supersedes": f.supersedes, "provenance": f.provenance,
            "salience": f.salience, "confidence": f.confidence,
        } for f in src._all_facts()]
        dst = _mk_memory()
        stats = dst.import_snapshot({"engram_export_version": 1, "facts": facts, "episodes": []},
                                    user_id="u2")
        assert stats["facts_restored"] == len(facts) > 0
        assert stats["malformed"] == 0

    def test_malformed_rows_are_counted_not_fatal(self):
        dst = _mk_memory()
        stats = dst.import_snapshot({
            "engram_export_version": 1,
            "facts": [{"subject": "a"}, "not-a-dict", {"subject": "a", "predicate": "p", "object": "o"}],
            "episodes": [{"no_content": True}, "junk"],
        })
        assert stats["facts_restored"] == 1 and stats["malformed"] == 2
        assert stats["episodes_restored"] == 0


pytest_fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    d = tempfile.mkdtemp(prefix="engram_snap_srv_")
    os.environ.update(ENGRAM_DATA_DIR=d, ENGRAM_EMBEDDER="hashing", ENGRAM_OPEN="1")
    os.environ.pop("ENGRAM_LLM", None)
    os.environ.pop("ENGRAM_API_KEYS", None)
    os.environ.pop("ENGRAM_ALLOW_ANONYMOUS", None)
    from engram.server import app as appmod
    appmod._svc = None
    with TestClient(appmod.app) as c:
        yield c
    shutil.rmtree(d, ignore_errors=True)


def _hdr(ns: str) -> dict:
    return {"Authorization": f"Bearer {ns}"}


def test_http_unparseable_import_is_400_not_500(client):
    # Any JSON object that matches no known export shape used to crash the parser -> 500.
    r = client.post("/v1/import", json={"data": {"foo": "bar"}}, headers=_hdr("qa"))
    assert r.status_code == 400
    assert "could not parse" in r.json()["detail"]


def test_http_export_import_roundtrip(client):
    r = client.post("/v1/remember", json={"content": "My cat is named Miso."}, headers=_hdr("src"))
    assert r.status_code == 200
    snap = client.get("/v1/export", params={"include_sensitive": "true"}, headers=_hdr("src")).json()
    r = client.post("/v1/import", json={"data": snap}, headers=_hdr("dst"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["format"] == "engram" and body["facts_restored"] >= 1
    ctx = client.post("/v1/recall", json={"query": "what is my cat's name?"},
                      headers=_hdr("dst")).json()["context"]
    assert "Miso" in ctx
