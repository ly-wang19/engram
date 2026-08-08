from __future__ import annotations

import json

import pytest

from engram import Memory
from engram.store import StoreFormatError, load_memory


def _write_legacy_store(path, contents, declared_count=None):
    source = Memory()
    episodes = [source.add(content, user_id="u") for content in contents]
    path.mkdir()
    (path / "episodes.jsonl").write_text(
        "".join(json.dumps(ep.__dict__) + "\n" for ep in episodes), encoding="utf-8"
    )
    counts = {name: 0 for name in ("episodes", "facts", "entities", "relations", "working", "conflicts")}
    counts["episodes"] = len(episodes) if declared_count is None else declared_count
    (path / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "counts": counts, "state": {}}), encoding="utf-8"
    )


def test_torn_trailing_jsonl_record_beyond_manifest_is_ignored(tmp_path):
    path = tmp_path / "store"
    _write_legacy_store(path, ["I live in Shenzhen.", "I work at Moonshot AI."])

    with (path / "episodes.jsonl").open("ab") as fh:
        fh.write(b'{"id":"half"')

    loaded = Memory.open(str(path))
    assert len(loaded.episodes_doc.values()) == 2
    assert {e.content for e in loaded.episodes_doc.values()} == {
        "I live in Shenzhen.",
        "I work at Moonshot AI.",
    }


def test_missing_committed_jsonl_record_fails_loudly(tmp_path):
    path = tmp_path / "store"
    _write_legacy_store(path, ["I live in Shenzhen.", "I work at Moonshot AI."], declared_count=3)

    with pytest.raises(StoreFormatError):
        Memory.open(str(path))


def test_failed_load_does_not_clear_existing_memory(tmp_path):
    existing = Memory()
    existing.add("I live in Hangzhou.", user_id="u")
    before = [e.content for e in existing.episodes_doc.values()]

    bad_path = tmp_path / "bad-store"
    _write_legacy_store(bad_path, ["I work at Moonshot AI."], declared_count=2)

    with pytest.raises(StoreFormatError):
        load_memory(existing, str(bad_path))

    assert [e.content for e in existing.episodes_doc.values()] == before
