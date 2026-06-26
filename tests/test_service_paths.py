from __future__ import annotations

from engram import Memory
from engram.service import MemoryService


def test_service_uses_directory_namespace_path(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    path = svc._path("alice@example.com")
    assert path == str(tmp_path / "aliceexample.com")
    assert not path.endswith(".pkl")


def test_service_loads_legacy_pkl_named_store_and_forget_removes_both(tmp_path):
    legacy = tmp_path / "alice.pkl"
    mem = Memory()
    mem.add("I live in Shenzhen.", user_id="alice")
    mem.save(str(legacy))

    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    loaded = svc.get("alice")
    assert loaded.episodes_doc.values()[0].content == "I live in Shenzhen."

    new_path = tmp_path / "alice"
    new_path.mkdir()
    assert legacy.exists() and new_path.exists()
    assert svc.forget("alice")["ok"] is True
    assert not legacy.exists()
    assert not new_path.exists()
