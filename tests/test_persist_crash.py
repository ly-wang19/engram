from __future__ import annotations

import json

import pytest

from engram import Memory
from engram.store import StoreFormatError, load_memory


def test_torn_trailing_jsonl_record_beyond_manifest_is_ignored(tmp_path):
    mem = Memory()
    mem.add("I live in Shenzhen.", user_id="u")
    mem.add("I work at Moonshot AI.", user_id="u")
    path = tmp_path / "store"
    mem.save(str(path))

    with (path / "episodes.jsonl").open("ab") as fh:
        fh.write(b'{"id":"half"')

    loaded = Memory.open(str(path))
    assert len(loaded.episodes_doc.values()) == 2
    assert {e.content for e in loaded.episodes_doc.values()} == {
        "I live in Shenzhen.",
        "I work at Moonshot AI.",
    }


def test_missing_committed_jsonl_record_fails_loudly(tmp_path):
    mem = Memory()
    mem.add("I live in Shenzhen.", user_id="u")
    mem.add("I work at Moonshot AI.", user_id="u")
    path = tmp_path / "store"
    mem.save(str(path))

    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["counts"]["episodes"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StoreFormatError):
        Memory.open(str(path))


def test_failed_load_does_not_clear_existing_memory(tmp_path):
    existing = Memory()
    existing.add("I live in Hangzhou.", user_id="u")
    before = [e.content for e in existing.episodes_doc.values()]

    source = Memory()
    source.add("I work at Moonshot AI.", user_id="u")
    bad_path = tmp_path / "bad-store"
    source.save(str(bad_path))

    manifest_path = bad_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["counts"]["episodes"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StoreFormatError):
        load_memory(existing, str(bad_path))

    assert [e.content for e in existing.episodes_doc.values()] == before
