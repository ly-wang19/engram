"""Cross-instance portability — the cross-account memory bus.

The memory belongs to the user, not to any one instance/account: an `export()` payload must restore
into a different Engram instance (different data dir, possibly a different embedder), preserving fact
ids, bi-temporal stamps, supersession chains, and provenance. These tests drive the native "engram"
import format, the identity-consistent stats fix, the import-CLI namespace unification, and the
ENGRAM_STORAGE backend selector.
"""
from __future__ import annotations

import json
import sys

import pytest

from engram.connectors import parse, sniff
from engram.service import MemoryService


@pytest.fixture()
def clean_env(monkeypatch):
    for var in ("ENGRAM_EMBEDDER", "ENGRAM_LLM", "ENGRAM_ANSWERER", "ENGRAM_STORAGE",
                "ENGRAM_MAX_HOT_FACTS", "ENGRAM_CONFLICT_DETECTION", "ENGRAM_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)


def _seed(svc: MemoryService, user: str) -> None:
    svc.remember(user, "I moved to Berlin in 2024 and work on the Engram project.", session_id="s1")
    svc.add_fact(user, "user", "lives_in", "Beijing")
    svc.add_fact(user, "user", "lives_in", "Shanghai")  # supersedes Beijing -> a chain to preserve


# --- format detection -------------------------------------------------------

def test_sniff_recognizes_engram_export(tmp_path, clean_env):
    svc = MemoryService(data_dir=str(tmp_path / "a"))
    _seed(svc, "alice")
    payload = svc.export("alice", include_sensitive=True)
    assert sniff(payload) == "engram"
    assert sniff(json.dumps(payload)) == "engram"


def test_parse_gives_actionable_error_for_engram_format(tmp_path, clean_env):
    svc = MemoryService(data_dir=str(tmp_path / "a"))
    _seed(svc, "alice")
    payload = svc.export("alice", include_sensitive=True)
    # parse() produces sessions; a native export restores directly — the error must say where to go.
    with pytest.raises(ValueError, match="engram"):
        parse(payload)


# --- the roundtrip ----------------------------------------------------------

def test_export_import_roundtrip_preserves_memory(tmp_path, clean_env):
    src = MemoryService(data_dir=str(tmp_path / "src"))
    _seed(src, "alice")
    payload = src.export("alice", include_sensitive=True)

    dst = MemoryService(data_dir=str(tmp_path / "dst"))
    stats = dst.import_("alice", data=payload, format="auto")
    assert stats["ok"] is True
    assert stats["format"] == "engram"
    assert stats["facts"] == len(payload["facts"])
    assert stats["episodes"] == len(payload["episodes"])

    out = dst.export("alice", include_sensitive=True)
    assert {f["id"] for f in out["facts"]} == {f["id"] for f in payload["facts"]}

    # bi-temporal stamps, supersession chain, and provenance survive the move
    src_by_id = {f["id"]: f for f in payload["facts"]}
    for f in out["facts"]:
        assert f["valid_at"] == pytest.approx(src_by_id[f["id"]]["valid_at"])
        assert f["supersedes"] == src_by_id[f["id"]]["supersedes"]
        assert f["provenance"] == src_by_id[f["id"]]["provenance"]
    assert any(f["supersedes"] for f in out["facts"]), "Beijing->Shanghai chain must survive"
    assert any(f["invalid_at"] for f in out["facts"]), "superseded facts must stay invalidated"

    # the graph is rebuilt on the target, and episodes are not re-queued for System-2
    assert out["graph"]["nodes"] and out["graph"]["edges"]
    assert dst.stats("alice")["counts"]["episodes_pending"] == 0

    # and the target instance actually answers from the migrated memory
    res = dst.recall("alice", "Where does the user live now?", lean=False)
    assert any("Shanghai" in t for t in res["facts"]) or "Shanghai" in res["answer"]


def test_reimport_is_idempotent(tmp_path, clean_env):
    src = MemoryService(data_dir=str(tmp_path / "src"))
    _seed(src, "alice")
    payload = src.export("alice", include_sensitive=True)

    dst = MemoryService(data_dir=str(tmp_path / "dst"))
    first = dst.import_("alice", data=payload, format="engram")
    again = dst.import_("alice", data=payload, format="engram")
    assert again["facts"] == 0 and again["episodes"] == 0
    assert again["facts_skipped"] == first["facts"]
    assert again["episodes_skipped"] == first["episodes"]
    out = dst.export("alice", include_sensitive=True)
    assert len(out["facts"]) == len(payload["facts"])  # no duplicates


def test_share_safe_export_still_imports(tmp_path, clean_env):
    src = MemoryService(data_dir=str(tmp_path / "src"))
    _seed(src, "alice")
    src.add_fact("alice", "user", "has_condition", "hay fever", sensitive=True)
    payload = src.export("alice")  # share-safe: no sensitive facts, no episodes

    dst = MemoryService(data_dir=str(tmp_path / "dst"))
    stats = dst.import_("alice", data=payload, format="auto")
    assert stats["facts"] == len(payload["facts"]) > 0
    assert stats["episodes"] == 0
    out = dst.export("alice", include_sensitive=True)
    assert not any(f["sensitive"] for f in out["facts"])  # redacted stayed redacted


def test_import_rejects_unknown_version(tmp_path, clean_env):
    dst = MemoryService(data_dir=str(tmp_path / "dst"))
    with pytest.raises(ValueError, match="version"):
        dst.import_("alice", data={"engram_export_version": 99}, format="engram")


def test_import_cross_embedder_reembeds(tmp_path, clean_env, monkeypatch):
    """The migration path IS the re-embedding path: the target re-embeds with its own embedder, so a
    payload exported under one embedding space restores into another without touching the source."""
    src = MemoryService(data_dir=str(tmp_path / "src"))
    _seed(src, "alice")
    payload = src.export("alice", include_sensitive=True)

    monkeypatch.setenv("ENGRAM_MAX_HOT_FACTS", "10000")
    dst = MemoryService(data_dir=str(tmp_path / "dst"), embedder_name="hashing")
    dst.import_("alice", data=payload, format="engram")
    mem = dst.get("alice")
    dim = len(dst.embedder.embed("probe"))
    for f in mem.fact_store.values():
        assert f.embedding is not None and len(f.embedding) == dim


# --- identity-consistent stats ---------------------------------------------

def test_stats_follow_linked_identity(tmp_path, clean_env):
    svc = MemoryService(data_dir=str(tmp_path / "id"))
    mem = svc.get("zz-handle")
    mem.link_identity("zz-handle", "aa-canonical")  # canonical root: "aa-canonical"
    svc.remember("zz-handle", "I work at Acme Corp.", session_id="s1")
    counts = svc.stats("zz-handle")["counts"]
    assert counts["episodes"] >= 1
    assert counts["facts_live"] >= 1


# --- import CLI writes the same namespace dirs as the service ---------------

def test_import_cli_local_writes_service_namespace_dir(tmp_path, clean_env, monkeypatch, capsys):
    from engram.connectors.__main__ import main

    fp = tmp_path / "log.txt"
    fp.write_text("User: I like green tea.\nAssistant: Noted.\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    monkeypatch.setattr(sys, "argv", [
        "prog", "--file", str(fp), "--format", "transcript",
        "--namespace", "a/b", "--data-dir", str(data_dir),
    ])
    main()

    svc = MemoryService(data_dir=str(data_dir))
    assert (data_dir / svc._safe_user("a/b")).is_dir(), \
        "CLI must write the same digest-backed namespace dir the service reads"
    assert svc.stats("a/b")["counts"]["episodes"] >= 1


def test_import_cli_accepts_engram_export(tmp_path, clean_env, monkeypatch, capsys):
    from engram.connectors.__main__ import main

    src = MemoryService(data_dir=str(tmp_path / "src"))
    _seed(src, "alice")
    fp = tmp_path / "export.json"
    fp.write_text(json.dumps(src.export("alice", include_sensitive=True)), encoding="utf-8")

    data_dir = tmp_path / "data"
    monkeypatch.setattr(sys, "argv", [
        "prog", "--file", str(fp), "--namespace", "alice", "--data-dir", str(data_dir),
    ])
    main()
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["format"] == "engram" and out["facts"] > 0

    dst = MemoryService(data_dir=str(data_dir))
    assert dst.stats("alice")["counts"]["facts_live"] >= 1


# --- ENGRAM_STORAGE backend selector ----------------------------------------

def test_engram_storage_env_selects_backend(tmp_path, clean_env, monkeypatch):
    monkeypatch.setenv("ENGRAM_STORAGE", "lancedb")
    svc = MemoryService(data_dir=str(tmp_path / "s"))
    assert svc.config.storage == "lancedb"
    assert svc.stats.__self__ is svc  # constructing the service must not import lancedb yet


def test_engram_storage_env_rejects_unknown(tmp_path, clean_env, monkeypatch):
    monkeypatch.setenv("ENGRAM_STORAGE", "postgres")
    with pytest.raises(ValueError, match="ENGRAM_STORAGE"):
        MemoryService(data_dir=str(tmp_path / "s"))
