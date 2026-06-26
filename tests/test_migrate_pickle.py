from __future__ import annotations

import pickle

from engram import Memory
from engram.store.migrate import migrate


def _legacy_dict(mem: Memory) -> dict:
    return {
        "episodes_doc": mem.episodes_doc,
        "episodes_vec": mem.episodes_vec,
        "fact_store": mem.fact_store,
        "cold_store": mem.cold_store,
        "summary_vec": mem.summary_vec,
        "graph": mem.graph,
        "resolver": mem.resolver,
        "persona_cache": mem._persona_cache,
        "focus": mem.focus,
        "policy": mem.policy,
        "working_mem": mem.working_mem,
        "identity": mem._identity,
        "aliases": mem._aliases,
        "conflicts": mem.conflicts,
    }


def test_migrate_pickle_dry_run_reports_counts_and_writes_nothing(tmp_path):
    mem = Memory()
    mem.add("My name is Wei and I work at Tencent.", user_id="u")
    mem.consolidate()
    src = tmp_path / "legacy.pkl"
    dst = tmp_path / "new-store"
    src.write_bytes(pickle.dumps(_legacy_dict(mem)))

    out = migrate(str(src), str(dst), dry_run=True)
    assert out["ok"] and out["dry_run"] is True and out["written"] is False
    assert out["counts"]["episodes"] == 1
    assert out["counts"]["facts"] >= 1
    assert not dst.exists()


def test_migrate_pickle_apply_writes_jsonl_store_loadable_by_memory_open(tmp_path):
    mem = Memory()
    mem.add("My name is Wei and I work at Tencent.", user_id="u")
    mem.add("Actually I now work at Moonshot AI.", user_id="u")
    mem.consolidate()
    src = tmp_path / "legacy.pkl"
    dst = tmp_path / "new-store"
    src.write_bytes(pickle.dumps(_legacy_dict(mem)))

    out = migrate(str(src), str(dst))
    assert out["written"] is True
    assert (dst / "manifest.json").exists()
    loaded = Memory.open(str(dst))
    assert len(loaded.episodes_doc.values()) == len(mem.episodes_doc.values())
    assert {f.object for f in loaded.fact_store.values()} == {f.object for f in mem.fact_store.values()}
    assert "moonshot" in loaded.search("Where do I work?", user_id="u").answer().lower()


def test_migrate_pickle_supports_legacy_tuple_shape(tmp_path):
    mem = Memory()
    mem.add("I live in Shenzhen.", user_id="u")
    legacy = (
        mem.episodes_doc,
        mem.episodes_vec,
        mem.fact_store,
        mem.cold_store,
        mem.summary_vec,
        mem.graph,
        mem.resolver,
        mem._persona_cache,
    )
    src = tmp_path / "legacy-tuple.pkl"
    dst = tmp_path / "tuple-store"
    src.write_bytes(pickle.dumps(legacy))

    out = migrate(str(src), str(dst))
    assert out["counts"]["episodes"] == 1
    assert Memory.open(str(dst)).episodes_doc.values()[0].content == "I live in Shenzhen."
