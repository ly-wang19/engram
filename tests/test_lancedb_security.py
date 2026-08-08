from __future__ import annotations

import builtins
import json
import os
import stat
import sys
from types import SimpleNamespace

import pytest

from engram.config import Config
from engram.memory import Memory
from engram.store.lancedb_store import LanceDBPathError, LanceDBVectorStore


class _EmptyLanceDB:
    def list_tables(self):
        return []


@pytest.fixture(autouse=True)
def fake_lancedb(monkeypatch):
    """Path/namespace checks do not require the optional native LanceDB package."""

    monkeypatch.setitem(sys.modules, "lancedb", SimpleNamespace(connect=lambda _path: _EmptyLanceDB()))


def test_lancedb_root_and_owner_marker_are_owner_only(tmp_path):
    root = tmp_path / "vectors"
    root.mkdir(mode=0o777)
    os.chmod(root, 0o777)

    store = LanceDBVectorStore(str(root), "facts", binding_id="canonical:test")
    try:
        marker = root / ".engram-lancedb.json"
        assert stat.S_IMODE(os.lstat(root).st_mode) == 0o700
        assert stat.S_IMODE(os.lstat(marker).st_mode) == 0o600
        assert json.loads(marker.read_text(encoding="utf-8")) == {
            "binding_id": "canonical:test",
            "schema": 1,
        }
    finally:
        store.close()


def test_lancedb_rejects_symlink_and_non_directory_roots(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(LanceDBPathError, match="symlink"):
        LanceDBVectorStore(str(link))
    cfg = Config(storage="lancedb", data_path=str(link))
    with pytest.raises(LanceDBPathError, match="symlink"):
        Memory.open(str(tmp_path / "snapshot"), config=cfg)

    regular = tmp_path / "regular"
    regular.write_text("not a directory", encoding="utf-8")
    with pytest.raises(LanceDBPathError, match="directory"):
        LanceDBVectorStore(str(regular))


def test_lancedb_rejects_nonempty_unmarked_legacy_root(tmp_path):
    root = tmp_path / "unknown-vectors"
    root.mkdir()
    (root / "foreign-data").write_text("unknown", encoding="utf-8")
    original_mode = stat.S_IMODE(os.lstat(root).st_mode)

    with pytest.raises(LanceDBPathError, match="non-empty unmarked"):
        LanceDBVectorStore(str(root), binding_id="canonical:test")
    assert stat.S_IMODE(os.lstat(root).st_mode) == original_mode

    base = tmp_path / "unknown-base"
    base.mkdir()
    (base / "legacy-lance-data").write_text("unknown", encoding="utf-8")
    cfg = Config(storage="lancedb", data_path=str(base))
    with pytest.raises(LanceDBPathError, match="non-empty unmarked"):
        Memory.open(str(tmp_path / "snapshot"), config=cfg)


def test_lancedb_rejects_marker_link_or_namespace_mismatch(tmp_path):
    outside = tmp_path / "outside-marker"
    outside.write_text('{"schema":1,"binding_id":"canonical:test"}', encoding="utf-8")
    linked_root = tmp_path / "linked-marker-root"
    linked_root.mkdir()
    (linked_root / ".engram-lancedb.json").symlink_to(outside)
    with pytest.raises(LanceDBPathError, match="single-link regular file"):
        LanceDBVectorStore(str(linked_root), binding_id="canonical:test")

    root = tmp_path / "bound"
    first = LanceDBVectorStore(str(root), binding_id="canonical:first")
    first.close()
    with pytest.raises(LanceDBPathError, match="different Engram namespace"):
        LanceDBVectorStore(str(root), binding_id="canonical:second")


def test_memory_open_rejects_symlinked_namespaces_component(tmp_path):
    base = tmp_path / "vectors"
    cfg = Config(storage="lancedb", data_path=str(base))
    initialized = Memory.open(str(tmp_path / "first-snapshot"), config=cfg)
    for name in ("episodes_vec", "fact_store", "cold_store", "summary_vec"):
        getattr(initialized, name).close()

    namespaces = base / "namespaces"
    relocated = base / "namespaces-real"
    namespaces.rename(relocated)
    namespaces.symlink_to(relocated, target_is_directory=True)
    with pytest.raises(LanceDBPathError, match="must not be a symlink"):
        Memory.open(str(tmp_path / "second-snapshot"), config=cfg)


def test_lancedb_rejects_unsafe_table_names(tmp_path):
    with pytest.raises(LanceDBPathError, match="table name"):
        LanceDBVectorStore(str(tmp_path / "vectors"), "../facts")


def test_missing_optional_dependency_does_not_create_storage(monkeypatch, tmp_path):
    root = tmp_path / "vectors"
    real_import = builtins.__import__

    def without_lancedb(name, *args, **kwargs):
        if name == "lancedb":
            raise ModuleNotFoundError("No module named 'lancedb'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_lancedb)
    with pytest.raises(ModuleNotFoundError, match="lancedb"):
        LanceDBVectorStore(str(root))
    assert not root.exists()


def test_memory_open_binds_explicit_base_to_canonical_snapshot(tmp_path):
    vector_base = tmp_path / "vectors"
    cfg = Config(storage="lancedb", data_path=str(vector_base))
    snapshot_a = tmp_path / "alice"
    snapshot_b = tmp_path / "bob"

    first = Memory.open(str(snapshot_a), config=cfg)
    reopened = Memory.open(str(snapshot_a), config=cfg)
    other = Memory.open(str(snapshot_b), config=cfg)
    try:
        assert first.fact_store.path == reopened.fact_store.path
        assert first.fact_store.path != other.fact_store.path
        assert os.path.commonpath([first.fact_store.path, str(vector_base)]) == str(vector_base)
        assert stat.S_IMODE(os.lstat(vector_base).st_mode) == 0o700
        assert stat.S_IMODE(os.lstat(first.fact_store.path).st_mode) == 0o700
        assert cfg.data_path == str(vector_base)

        base_marker_path = vector_base / ".engram-lancedb.json"
        first_marker_path = os.path.join(first.fact_store.path, ".engram-lancedb.json")
        other_marker_path = os.path.join(other.fact_store.path, ".engram-lancedb.json")
        base_marker = json.loads(base_marker_path.read_text(encoding="utf-8"))
        with open(first_marker_path, encoding="utf-8") as marker_file:
            first_marker = json.load(marker_file)
        with open(other_marker_path, encoding="utf-8") as marker_file:
            other_marker = json.load(marker_file)
        assert base_marker["binding_id"].startswith("base:")
        assert first_marker["binding_id"].startswith("canonical:")
        assert first_marker["binding_id"] != other_marker["binding_id"]
    finally:
        # Four logical stores share each root but own separate no-follow directory descriptors.
        for memory in (first, reopened, other):
            for name in ("episodes_vec", "fact_store", "cold_store", "summary_vec"):
                getattr(memory, name).close()


def test_memory_open_rejects_orphaned_vectors_without_canonical_snapshot(monkeypatch, tmp_path):
    cfg = Config(storage="lancedb", data_path=str(tmp_path / "vectors"))
    snapshot = tmp_path / "snapshot"
    orphaned = {"visible": False}
    original_values = LanceDBVectorStore.values

    def values(store):
        if orphaned["visible"] and store.table_name == "fact_store":
            return [object()]
        return original_values(store)

    monkeypatch.setattr(LanceDBVectorStore, "values", values)
    clean = Memory.open(str(snapshot), config=cfg)
    for name in ("episodes_vec", "fact_store", "cold_store", "summary_vec"):
        getattr(clean, name).close()

    orphaned["visible"] = True
    with pytest.raises(LanceDBPathError, match="orphaned vectors"):
        Memory.open(str(snapshot), config=cfg)
