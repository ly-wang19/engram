from __future__ import annotations

import sys

from engram import Memory


def test_default_memory_does_not_import_lancedb(tmp_path):
    sys.modules.pop("lancedb", None)
    mem = Memory()
    mem.add("I like Python.", user_id="u")
    mem.save(str(tmp_path / "store"))
    assert "lancedb" not in sys.modules


def test_default_save_uses_sqlite_manifest(tmp_path):
    path = tmp_path / "store"
    mem = Memory()
    mem.add("I like Python.", user_id="u")
    mem.save(str(path))
    assert (path / "manifest.json").exists()
    assert (path / "store.sqlite3").exists()
    assert not (path / "episodes.jsonl").exists()
