"""Offline failure injection for repeated JSONL snapshot saves."""
from __future__ import annotations

import pytest

from engram import Memory
from engram.store import persist


def _snapshot(mem):
    return {
        "episodes": sorted((e.id, e.content) for e in mem.episodes_doc.values()),
        "facts": sorted((f.id, f.text) for f in mem.fact_store.values()),
        "focus": mem.get_focus(),
    }


def _memory():
    mem = Memory()
    mem.add("Synthetic original episode.", user_id="synthetic-user")
    mem.add_fact("synthetic-user", "prefers", "green", user_id="synthetic-user")
    mem.set_focus(track=["original"])
    return mem


@pytest.mark.parametrize("failure", [OSError, SystemExit], ids=["io-error", "process-exit"])
def test_interrupted_resave_recovers_last_committed_snapshot(tmp_path, monkeypatch, failure):
    mem = _memory()
    path = str(tmp_path / "store")
    mem.save(path)
    committed = _snapshot(mem)
    mem.episodes_doc.values()[0].content = "Synthetic replacement episode."
    mem.set_focus(track=["replacement"])
    write = persist._write_jsonl

    def fail_after_first_collection(target, name, rows):
        if name == "facts":
            raise failure("injected failure after episodes replacement")
        return write(target, name, rows)

    with monkeypatch.context() as patch:
        patch.setattr(persist, "_write_jsonl", fail_after_first_collection)
        with pytest.raises(failure):
            mem.save(path)

    # A fresh open must never expose new episodes alongside the old manifest/state.
    assert _snapshot(Memory.open(path)) == committed
    # Retrying the complete snapshot remains safe and does not duplicate records.
    mem.save(path)
    mem.save(path)
    assert _snapshot(Memory.open(path)) == _snapshot(mem)


def test_failed_shrinking_snapshot_keeps_committed_records_readable(tmp_path, monkeypatch):
    mem = _memory()
    mem.add("Synthetic second episode.", user_id="synthetic-user")
    path = str(tmp_path / "store")
    mem.save(path)
    committed = _snapshot(mem)
    mem.episodes_doc.delete(mem.episodes_doc.values()[0].id)
    write = persist._write_jsonl

    def fail_after_first_collection(target, name, rows):
        if name == "facts":
            raise OSError("injected failure after shortened episodes replacement")
        return write(target, name, rows)

    with monkeypatch.context() as patch:
        patch.setattr(persist, "_write_jsonl", fail_after_first_collection)
        with pytest.raises(OSError):
            mem.save(path)
    assert _snapshot(Memory.open(path)) == committed


def test_interrupted_first_save_recovers_empty_store(tmp_path, monkeypatch):
    mem = _memory()
    path = tmp_path / "store"
    write = persist._write_jsonl

    def fail_after_first_collection(target, name, rows):
        if name == "facts":
            raise SystemExit("injected first-save process exit")
        return write(target, name, rows)

    with monkeypatch.context() as patch:
        patch.setattr(persist, "_write_jsonl", fail_after_first_collection)
        with pytest.raises(SystemExit):
            mem.save(str(path))
    assert _snapshot(Memory.open(str(path)))["episodes"] == []
    assert not (path / "episodes.jsonl").exists()
    mem.save(str(path))
    assert _snapshot(Memory.open(str(path))) == _snapshot(mem)


def test_interrupted_recovery_remains_retryable_and_preserves_permissions(tmp_path, monkeypatch):
    import os
    import stat

    mem = _memory()
    path = tmp_path / "store"
    mem.save(str(path))
    (path / "episodes.jsonl").chmod(0o600)
    committed = _snapshot(mem)
    mem.episodes_doc.values()[0].content = "Synthetic interrupted replacement."
    write = persist._write_jsonl

    def fail_after_first_collection(target, name, rows):
        if name == "facts":
            raise SystemExit("injected writer exit")
        return write(target, name, rows)

    with monkeypatch.context() as patch:
        patch.setattr(persist, "_write_jsonl", fail_after_first_collection)
        with pytest.raises(SystemExit):
            mem.save(str(path))

    replace = os.replace

    def fail_during_recovery(source, target):
        if str(source).endswith("facts.jsonl.recover"):
            raise OSError("injected recovery interruption")
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_during_recovery)
        with pytest.raises(OSError, match="recovery interruption"):
            Memory.open(str(path))

    assert _snapshot(Memory.open(str(path))) == committed
    assert stat.S_IMODE((path / "episodes.jsonl").stat().st_mode) == 0o600
    mem.save(str(path))
    assert _snapshot(Memory.open(str(path))) == _snapshot(mem)


def test_hard_process_exit_during_resave_recovers_snapshot(tmp_path):
    import subprocess
    import sys

    mem = _memory()
    path = str(tmp_path / "store")
    mem.save(path)
    committed = _snapshot(mem)
    script = """
import os
import sys
from engram import Memory
from engram.store import persist
mem = Memory.open(sys.argv[1])
mem.episodes_doc.values()[0].content = 'Synthetic hard-exit replacement.'
write = persist._write_jsonl
def interrupted(path, name, rows):
    if name == 'facts':
        os._exit(31)
    return write(path, name, rows)
persist._write_jsonl = interrupted
mem.save(sys.argv[1])
"""
    result = subprocess.run([sys.executable, "-c", script, path], capture_output=True, text=True)
    assert result.returncode == 31, result.stderr
    assert _snapshot(Memory.open(path)) == committed
