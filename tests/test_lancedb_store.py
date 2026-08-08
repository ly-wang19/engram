from __future__ import annotations

import json
import sys

import pytest

pytest.importorskip("lancedb")

from engram.config import Config
from engram.memory import Memory
from engram.store.lancedb_store import LanceDBVectorStore
from engram.store.memory_store import InMemoryVectorStore
from engram.types import Fact


def _fact(subject: str, predicate: str, obj: str, vec: list[float]) -> Fact:
    f = Fact(subject=subject, predicate=predicate, object=obj, user_id="u", embedding=vec)
    return f


def test_lancedb_where_filter_matches_in_memory_when_hits_are_far(tmp_path):
    mem_store = InMemoryVectorStore()
    lance = LanceDBVectorStore(str(tmp_path / "lancedb"), "facts")
    query = [1.0, 0.0]

    for i in range(12):
        other = Fact(
            subject="other",
            predicate="near",
            object=str(i),
            user_id="other",
            embedding=[1.0, 0.0],
        )
        mem_store.upsert(other.id, other.embedding, other)
        lance.upsert(other.id, other.embedding, other)

    target = _fact("user", "likes", "bananas", [0.0, 1.0])
    mem_store.upsert(target.id, target.embedding, target)
    lance.upsert(target.id, target.embedding, target)

    expected = [f.id for _, f in mem_store.search(query, 1, where=lambda f: f.user_id == "u")]
    actual = [f.id for _, f in lance.search(query, 1, where=lambda f: f.user_id == "u")]
    assert actual == expected == [target.id]


def test_lancedb_vector_store_persists_across_restart(tmp_path):
    store = LanceDBVectorStore(str(tmp_path / "lancedb"), "facts")
    fact = _fact("user", "works_at", "Moonshot AI", [1.0, 0.0])
    store.upsert(fact.id, fact.embedding, fact)

    reopened = LanceDBVectorStore(str(tmp_path / "lancedb"), "facts")
    hit = reopened.get(fact.id)
    assert isinstance(hit, Fact)
    assert hit.id == fact.id and hit.object == "Moonshot AI"
    assert reopened.search([1.0, 0.0], 1)[0][1].id == fact.id


def test_lancedb_topk_matches_in_memory_for_normalized_vectors(tmp_path):
    facts = [
        _fact("user", "likes", "Python", [1.0, 0.0, 0.0]),
        _fact("user", "likes", "Rust", [0.0, 1.0, 0.0]),
        _fact("user", "likes", "TypeScript", [0.0, 0.0, 1.0]),
    ]
    mem_store = InMemoryVectorStore()
    lance = LanceDBVectorStore(str(tmp_path / "lancedb"), "facts")
    for f in facts:
        mem_store.upsert(f.id, f.embedding, f)
        lance.upsert(f.id, f.embedding, f)

    query = [0.9, 0.1, 0.0]
    expected = [f.id for _, f in mem_store.search(query, 2, where=lambda f: f.user_id == "u")]
    actual = [f.id for _, f in lance.search(query, 2, where=lambda f: f.user_id == "u")]
    assert actual == expected


def test_memory_config_lancedb_uses_lancedb_stores_and_reopens(tmp_path):
    cfg = Config(storage="lancedb", data_path=str(tmp_path / "vectors"))
    snapshot = tmp_path / "snapshot"
    mem = Memory.open(str(snapshot), config=cfg)
    mem.add_fact("user", "works_at", "Moonshot AI", user_id="u")
    mem.save()
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["backend"] == "lancedb"

    reopened = Memory.open(str(snapshot), config=cfg)
    assert isinstance(reopened.fact_store, LanceDBVectorStore)
    assert reopened.fact_store.path == mem.fact_store.path
    assert "/namespaces/store-" in reopened.fact_store.path
    assert "moonshot" in reopened.search("Where do I work?", user_id="u").answer().lower()


def test_lancedb_consolidation_persists_invalidated_fact_payload(tmp_path):
    cfg = Config(storage="lancedb", data_path=str(tmp_path / "vectors"))
    snapshot = tmp_path / "snapshot"
    mem = Memory.open(str(snapshot), config=cfg)
    mem.add("My name is Finn and I work at IBM.", user_id="u", event_time=1.0)
    mem.add("I now work at Oracle.", user_id="u", event_time=2.0)
    mem.consolidate()
    mem.save()

    reopened = Memory.open(str(snapshot), config=cfg)
    history = reopened.history("Finn", "works_at", user_id="u")
    assert {f.object for f in history} == {"IBM", "Oracle"}
    assert [f.object for f in history if f.is_live()] == ["Oracle"]
    assert "oracle" in reopened.search("Where does Finn work?", user_id="u").answer().lower()


def test_lancedb_explicit_base_isolates_canonical_snapshots(tmp_path):
    cfg = Config(storage="lancedb", data_path=str(tmp_path / "vectors"))
    alice = Memory.open(str(tmp_path / "alice"), config=cfg)
    bob = Memory.open(str(tmp_path / "bob"), config=cfg)
    alice.add_fact("user", "likes", "apples", user_id="alice")
    bob.add_fact("user", "likes", "bananas", user_id="bob")
    alice.save()
    bob.save()

    assert alice.fact_store.path != bob.fact_store.path
    assert [fact.object for fact in alice.fact_store.values()] == ["apples"]
    assert [fact.object for fact in bob.fact_store.values()] == ["bananas"]


def test_lancedb_open_without_data_path_isolates_each_store(tmp_path):
    cfg = Config(storage="lancedb")
    a = Memory.open(str(tmp_path / "alice"), config=cfg)
    b = Memory.open(str(tmp_path / "bob"), config=cfg)
    a.add_fact("user", "likes", "apples", user_id="alice")
    b.add_fact("user", "likes", "bananas", user_id="bob")
    a.save()
    b.save()

    alice = Memory.open(str(tmp_path / "alice"), config=cfg)
    bob = Memory.open(str(tmp_path / "bob"), config=cfg)
    assert [f.object for f in alice.fact_store.values()] == ["apples"]
    assert [f.object for f in bob.fact_store.values()] == ["bananas"]
    assert "apples" in alice.search("What do I like?", user_id="alice").answer().lower()
    assert "bananas" in bob.search("What do I like?", user_id="bob").answer().lower()
    assert (tmp_path / "alice" / "lancedb").exists()
    assert (tmp_path / "bob" / "lancedb").exists()
    assert cfg.data_path is None


def test_lancedb_direct_construction_without_data_path_uses_isolated_temp_dirs():
    cfg = Config(storage="lancedb")
    a = Memory(config=cfg)
    b = Memory(config=cfg)
    assert isinstance(a.fact_store, LanceDBVectorStore)
    assert isinstance(b.fact_store, LanceDBVectorStore)
    assert a.fact_store.path != b.fact_store.path
    assert ".engram/data/default" not in a.fact_store.path
    assert cfg.data_path is None


def test_default_memory_path_does_not_import_lancedb(tmp_path):
    sys.modules.pop("lancedb", None)
    mem = Memory()
    mem.add("I like Python.", user_id="u")
    mem.save(str(tmp_path / "snapshot"))
    assert "lancedb" not in sys.modules
