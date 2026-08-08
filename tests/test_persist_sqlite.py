from __future__ import annotations

import json
import os
import sqlite3
import stat

import pytest

from engram import Memory
from engram.store import ConcurrentWriteError, StoreFormatError


def _write_legacy_jsonl_store(path, secret: str = "legacy private memory") -> None:
    mem = Memory()
    episode = mem.add(secret, user_id="u")
    path.mkdir()
    (path / "episodes.jsonl").write_text(
        json.dumps(episode.__dict__, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    counts = {
        "episodes": 1,
        "facts": 0,
        "entities": 0,
        "relations": 0,
        "working": 0,
        "conflicts": 0,
    }
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "backend": "durable",
                "counts": counts,
                "state": {"persona_cache": {"u": "sensitive cached persona"}},
            }
        ),
        encoding="utf-8",
    )


def test_save_is_incremental_upsert_and_delete_in_one_database(tmp_path):
    path = tmp_path / "store"
    mem = Memory()
    first = mem.add("first memory", user_id="u")
    mem.save(str(path))
    inode = os.stat(path / "store.sqlite3").st_ino

    with sqlite3.connect(path / "store.sqlite3") as conn:
        conn.executescript(
            "CREATE TABLE audit(op TEXT NOT NULL);"
            "CREATE TRIGGER audit_insert AFTER INSERT ON records "
            "BEGIN INSERT INTO audit VALUES('insert'); END;"
            "CREATE TRIGGER audit_update AFTER UPDATE ON records "
            "BEGIN INSERT INTO audit VALUES('update'); END;"
            "CREATE TRIGGER audit_delete AFTER DELETE ON records "
            "BEGIN INSERT INTO audit VALUES('delete'); END;"
        )

    mem.save(str(path))
    with sqlite3.connect(path / "store.sqlite3") as conn:
        assert conn.execute("SELECT op FROM audit").fetchall() == []

    second = mem.add("second memory", user_id="u")
    mem.save(str(path))
    with sqlite3.connect(path / "store.sqlite3") as conn:
        assert conn.execute("SELECT op FROM audit").fetchall() == [("insert",)]
        conn.execute("DELETE FROM audit")

    mem.episodes_doc.delete(first.id)
    mem.episodes_vec.delete(first.id)
    mem.save(str(path))
    with sqlite3.connect(path / "store.sqlite3") as conn:
        assert conn.execute("SELECT op FROM audit").fetchall() == [("delete",)]
        assert conn.execute(
            "SELECT id FROM records WHERE collection='episodes'"
        ).fetchall() == [(second.id,)]
    assert os.stat(path / "store.sqlite3").st_ino == inode


def test_uncommitted_sqlite_transaction_is_rolled_back_on_reopen(tmp_path):
    path = tmp_path / "store"
    mem = Memory()
    mem.add("committed memory", user_id="u")
    mem.save(str(path))

    conn = sqlite3.connect(path / "store.sqlite3")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE records SET payload=? WHERE collection='episodes'",
        (json.dumps({"id": "torn", "content": "uncommitted"}),),
    )
    conn.close()  # Simulates process loss before COMMIT.

    loaded = Memory.open(str(path))
    assert [item.content for item in loaded.episodes_doc.values()] == ["committed memory"]


def test_newer_database_generation_repairs_stale_manifest(tmp_path):
    path = tmp_path / "store"
    mem = Memory()
    mem.add("first committed memory", user_id="u")
    mem.save(str(path))
    stale_manifest = (path / "manifest.json").read_bytes()

    mem.add("second committed memory", user_id="u")
    mem.save(str(path))
    committed_manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    (path / "manifest.json").write_bytes(stale_manifest)  # crash before manifest replace

    loaded = Memory.open(str(path))
    assert {item.content for item in loaded.episodes_doc.values()} == {
        "first committed memory",
        "second committed memory",
    }
    repaired = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert repaired["generation"] == committed_manifest["generation"]
    assert repaired["counts"] == committed_manifest["counts"]


def test_first_commit_without_manifest_recovers_and_cannot_be_cleared(tmp_path, monkeypatch):
    import engram.store.persist as persist

    path = tmp_path / "store"
    writer = Memory()
    writer.add("canonical-memory-survives", user_id="u")
    with monkeypatch.context() as patcher:
        patcher.setattr(
            persist,
            "_write_manifest",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("manifest crash")),
        )
        with pytest.raises(RuntimeError, match="manifest crash"):
            writer.save(str(path))

    assert (path / "store.sqlite3").exists()
    assert not (path / "manifest.json").exists()
    with pytest.raises(ConcurrentWriteError, match="unbound Memory"):
        Memory().save(str(path))

    loaded = Memory.open(str(path))
    assert [ep.content for ep in loaded.episodes_doc.values()] == ["canonical-memory-survives"]
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generation"] == 1
    assert manifest["store_id"] == loaded._persist_store_id


def test_manifest_database_swap_is_rejected_even_when_generation_and_counts_match(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    for path, content in ((left, "left-memory"), (right, "right-memory")):
        mem = Memory()
        mem.add(content, user_id="u")
        mem.save(str(path))

    left_manifest = json.loads((left / "manifest.json").read_text(encoding="utf-8"))
    right_manifest = json.loads((right / "manifest.json").read_text(encoding="utf-8"))
    assert left_manifest["generation"] == right_manifest["generation"]
    assert left_manifest["counts"] == right_manifest["counts"]
    (left / "manifest.json").write_text(json.dumps(right_manifest), encoding="utf-8")

    with pytest.raises(StoreFormatError, match="store_id"):
        Memory.open(str(left))


def test_same_store_commit_id_mismatch_is_rejected(tmp_path):
    path = tmp_path / "store"
    mem = Memory()
    mem.add("committed", user_id="u")
    mem.save(str(path))
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["commit_id"] = "different-commit-at-same-generation"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StoreFormatError, match="commit_id"):
        Memory.open(str(path))


def test_manifest_generation_ahead_of_database_fails_loudly(tmp_path):
    path = tmp_path / "store"
    mem = Memory()
    mem.add("committed memory", user_id="u")
    mem.save(str(path))
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generation"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StoreFormatError, match="older than manifest"):
        Memory.open(str(path))


def test_stale_memory_instance_cannot_overwrite_newer_generation(tmp_path):
    path = tmp_path / "store"
    seed = Memory()
    seed.add("seed", user_id="u")
    seed.save(str(path))
    first = Memory.open(str(path))
    stale = Memory.open(str(path))

    first.add("winner", user_id="u")
    first.save()
    stale.add("must-not-overwrite", user_id="u")
    with pytest.raises(ConcurrentWriteError, match="stale Memory"):
        stale.save()

    reopened = Memory.open(str(path))
    contents = {ep.content for ep in reopened.episodes_doc.values()}
    assert contents == {"seed", "winner"}


def test_unbound_memory_cannot_overwrite_existing_store(tmp_path):
    path = tmp_path / "store"
    owner = Memory()
    owner.add("owned", user_id="u")
    owner.save(str(path))

    intruder = Memory()
    intruder.add("replacement", user_id="u")
    with pytest.raises(ConcurrentWriteError, match="unbound Memory"):
        intruder.save(str(path))
    assert [ep.content for ep in Memory.open(str(path)).episodes_doc.values()] == ["owned"]


def test_legacy_jsonl_migrates_once_without_plaintext_copy(tmp_path):
    path = tmp_path / "legacy-store"
    secret = "legacy-plaintext-secret-4281"
    _write_legacy_jsonl_store(path, secret)

    loaded = Memory.open(str(path))
    assert loaded.episodes_doc.values()[0].content == secret
    assert loaded._persona_cache == {"u": "sensitive cached persona"}
    assert (path / "store.sqlite3").exists()
    assert list(path.glob("*.jsonl")) == []
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["format"] == "sqlite"
    assert "state" not in manifest

    reopened = Memory.open(str(path))
    assert reopened.episodes_doc.values()[0].content == secret
    assert list(path.glob("*.jsonl")) == []


def test_interrupted_legacy_migration_is_idempotently_recovered(tmp_path, monkeypatch):
    import engram.store.persist as persist

    path = tmp_path / "legacy-store"
    _write_legacy_jsonl_store(path, "migration-recovery-secret")

    with monkeypatch.context() as patcher:
        patcher.setattr(
            persist,
            "_write_manifest",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated crash")),
        )
        with pytest.raises(RuntimeError, match="simulated crash"):
            Memory.open(str(path))
    assert (path / "store.sqlite3").exists()
    assert (path / "episodes.jsonl").exists()
    assert json.loads((path / "manifest.json").read_text(encoding="utf-8"))["schema_version"] == 1

    recovered = Memory.open(str(path))
    assert recovered.episodes_doc.values()[0].content == "migration-recovery-secret"
    assert list(path.glob("*.jsonl")) == []
    assert json.loads((path / "manifest.json").read_text(encoding="utf-8"))["schema_version"] == 2


@pytest.mark.parametrize(
    "owned_name",
    [
        "store.sqlite3",
        "manifest.json",
        ".lock",
        "episodes.jsonl",
        "facts.jsonl",
        "entities.jsonl",
        "relations.jsonl",
        "working.jsonl",
        "conflicts.jsonl",
    ],
)
def test_persistence_files_reject_symlinks_without_touching_victim(tmp_path, owned_name):
    path = tmp_path / "store"
    path.mkdir()
    victim = tmp_path / f"victim-{owned_name.replace('.', '_')}"
    victim.write_text("external-victim-content", encoding="utf-8")
    (path / owned_name).symlink_to(victim)

    with pytest.raises(StoreFormatError, match="direct regular file|safely open"):
        Memory.open(str(path))
    assert victim.read_text(encoding="utf-8") == "external-victim-content"


def test_store_directory_symlink_is_rejected(tmp_path):
    victim_dir = tmp_path / "victim-dir"
    victim_dir.mkdir()
    store_link = tmp_path / "store-link"
    store_link.symlink_to(victim_dir, target_is_directory=True)

    with pytest.raises(StoreFormatError, match="direct directory"):
        Memory.open(str(store_link))
    assert list(victim_dir.iterdir()) == []


def test_legacy_migration_rejects_collection_symlink_before_cleanup(tmp_path):
    path = tmp_path / "legacy-store"
    _write_legacy_jsonl_store(path, "legitimate legacy memory")
    victim = tmp_path / "outside-private-file"
    victim.write_text("do-not-overwrite", encoding="utf-8")
    (path / "facts.jsonl").symlink_to(victim)

    with pytest.raises(StoreFormatError, match="direct regular file"):
        Memory.open(str(path))
    assert victim.read_text(encoding="utf-8") == "do-not-overwrite"
    assert (path / "episodes.jsonl").read_text(encoding="utf-8")


def test_non_regular_persistence_artifact_is_rejected(tmp_path):
    path = tmp_path / "store"
    path.mkdir()
    (path / "manifest.json").mkdir()
    with pytest.raises(StoreFormatError, match="direct regular file"):
        Memory.open(str(path))


def test_owner_only_permissions_and_secure_delete(tmp_path):
    path = tmp_path / "store"
    mem = Memory()
    episode = mem.add("replace-me-secret-88426", user_id="u")
    mem.save(str(path))

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
    for filename in ("manifest.json", "store.sqlite3", ".lock"):
        assert stat.S_IMODE(os.stat(path / filename).st_mode) == 0o600

    episode.content = "erase-me-secret-99537"
    mem.save(str(path))
    assert b"replace-me-secret-88426" not in (path / "store.sqlite3").read_bytes()
    assert list(path.glob("store.sqlite3-journal")) == []

    mem.episodes_doc.delete(episode.id)
    mem.episodes_vec.delete(episode.id)
    mem.save(str(path))
    assert b"erase-me-secret-99537" not in (path / "store.sqlite3").read_bytes()
    assert list(path.glob("store.sqlite3-journal")) == []
