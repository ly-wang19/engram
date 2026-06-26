from __future__ import annotations

import json

from engram import Memory
from engram.types import Conflict
from engram.util import DAY

BASE = 1_700_000_000.0


def _build_memory() -> Memory:
    mem = Memory()
    mem.add("My name is Wei and I work at Tencent.", user_id="u1", event_time=BASE)
    mem.add("Actually I switched jobs and now work at Moonshot AI.", user_id="u1",
            event_time=BASE + DAY)
    mem.consolidate()
    mem.summarize_episodes(mem.episodes_doc.values())
    mem.link_identity("u1", "wei@example.com")
    mem.set_focus(track=["moonshot"], mute=["weight"])
    mem.set_policy(extract_instruction="only durable preferences")
    mem.remember_working("persist this temporary note", user_id="u1", session_id="s1")
    facts = sorted(mem.fact_store.values(), key=lambda f: f.valid_at)
    mem.conflicts["cf_test"] = Conflict(
        id="cf_test",
        older=facts[0].id,
        newer=facts[-1].id,
        text_older=facts[0].text,
        text_newer=facts[-1].text,
        user_id="u1",
        reason="test conflict audit",
    )
    mem._persona_cache["u1"] = "cached persona"
    return mem


def _facts(mem: Memory):
    return sorted(mem.fact_store.values() + mem.cold_store.values(), key=lambda f: f.id)


def test_jsonl_roundtrip_preserves_bitemporal_and_state(tmp_path):
    mem = _build_memory()
    path = str(tmp_path / "memory")
    mem.save(path)
    manifest = json.loads((tmp_path / "memory" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["backend"] == "durable"

    loaded = Memory.open(path)
    assert [e.__dict__ for e in sorted(loaded.episodes_doc.values(), key=lambda e: e.id)] == [
        e.__dict__ for e in sorted(mem.episodes_doc.values(), key=lambda e: e.id)
    ]
    assert [f.__dict__ for f in _facts(loaded)] == [f.__dict__ for f in _facts(mem)]
    assert [r.__dict__ for r in sorted(loaded.graph.relations(), key=lambda r: r.id)] == [
        r.__dict__ for r in sorted(mem.graph.relations(), key=lambda r: r.id)
    ]
    assert [w.__dict__ for w in loaded.working_mem.values()] == [w.__dict__ for w in mem.working_mem.values()]
    assert loaded.conflicts["cf_test"].__dict__ == mem.conflicts["cf_test"].__dict__
    assert loaded.get_focus() == mem.get_focus()
    assert loaded.get_policy()["policy"] == mem.get_policy()["policy"]
    assert loaded._persona_cache == {"u1": "cached persona"}
    assert loaded.resolver.resolve("wei@example.com") == loaded.resolver.resolve("u1")
    assert "moonshot" in loaded.search("Where does Wei work?", user_id="wei@example.com").answer().lower()


def test_memory_open_missing_store_starts_empty(tmp_path):
    mem = Memory.open(str(tmp_path / "new-store"))
    assert mem.episodes_doc.values() == []
    assert mem.fact_store.values() == []
