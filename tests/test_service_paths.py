from __future__ import annotations

from engram import Memory
from engram.embed import HashingEmbedder
from engram.llm.providers import make_embedder
from engram.service import MemoryService
from engram.types import Entity, Relation


def test_make_embedder_defaults_to_offline_hashing():
    assert isinstance(make_embedder(), HashingEmbedder)


def test_service_defaults_to_offline_hashing_embedder(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_EMBEDDER", raising=False)
    svc = MemoryService(data_dir=str(tmp_path))
    assert isinstance(svc.embedder, HashingEmbedder)


def test_service_reads_max_hot_facts_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_MAX_HOT_FACTS", "3")
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    assert svc.config.max_hot_facts == 3


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


def test_service_stats_reports_consolidation_backlog_without_content(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    mem.add("I live in Shenzhen.", user_id="u")
    mem.remember("Today my throat hurts.", user_id="u", scope="working")

    before = svc.stats("u")
    assert before["counts"]["episodes"] == 2
    assert before["counts"]["episodes_pending"] == 1
    assert before["counts"]["episodes_consolidated"] == 1
    assert before["counts"]["episodes_ephemeral"] == 1
    assert before["counts"]["working_live"] == 1
    assert before["consolidation_backlog"] is True
    assert "Shenzhen" not in str(before)
    assert "throat" not in str(before).lower()

    mem.consolidate()
    after = svc.stats("u")
    assert after["counts"]["episodes_pending"] == 0
    assert after["counts"]["episodes_consolidated"] == 2
    assert after["consolidation_backlog"] is False


def test_service_stats_reports_graph_hygiene_without_content(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    mem.add_fact("user", "works_at", "Acme", user_id="u")
    orphan = mem.graph.upsert_entity(Entity(name="Private Orphan", user_id="u"))
    subject = mem.graph.upsert_entity(Entity(name="ghost subject", user_id="u"))
    obj = mem.graph.upsert_entity(Entity(name="ghost object", user_id="u"))
    mem.graph.add_relation(Relation(subject_id=subject.id, predicate="ghost", object_id=obj.id, fact_id="missing_fact"))

    stats = svc.stats("u")

    assert stats["counts"]["graph_orphan_entities"] == 1
    assert stats["counts"]["graph_stale_relations"] == 1
    rendered = str(stats).lower()
    assert "private orphan" not in rendered
    assert "ghost subject" not in rendered
    assert "ghost object" not in rendered


def test_service_stats_counts_hot_and_cold_facts(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    for i in range(5):
        f = mem.add_fact("project", f"note_{i}", f"value-{i}", user_id="u")
        f.salience = float(i)

    assert mem.evict_cold(max_hot=2) == 3
    stats = svc.stats("u")
    assert stats["counts"]["facts_hot"] == 2
    assert stats["counts"]["facts_cold"] == 3
    assert stats["counts"]["cold_pages_out"] == 3
    assert stats["counts"]["cold_pages_in"] == 0
    assert stats["counts"]["facts_live"] == 5
    assert stats["counts"]["facts_superseded"] == 0
    assert "value-" not in str(stats)

    assert mem.evict_cold(max_hot=0) == 2
    mem.search("value-0", user_id="u")
    stats = svc.stats("u")
    assert stats["counts"]["cold_pages_in"] >= 1


def test_service_memories_and_export_include_cold_facts(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    cold = mem.add_fact("project", "project_note", "cold-value", user_id="u")
    hot = mem.add_fact("project", "project_note_2", "hot-value", user_id="u")
    cold.salience = 0.0
    hot.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1
    assert mem.cold_store.get(cold.id) is not None

    memories = svc.memories("u")
    assert memories["counts"]["facts_live"] == 2
    assert {f["object"] for f in memories["facts"]} == {"cold-value", "hot-value"}

    exported = svc.export("u")
    assert {f["object"] for f in exported["facts"]} == {"cold-value", "hot-value"}


def test_service_fact_edit_delete_persists_clean_graph_files(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    deleted = svc.add_fact("u", "user", "has_disease", "diabetes")["id"]
    edited = svc.add_fact("u", "user", "works_at", "ByteDance")["id"]

    assert svc.update_fact("u", edited, object="Moonshot AI")["ok"] is True
    assert svc.delete_fact("u", deleted)["ok"] is True

    store = tmp_path / "u"
    entities_jsonl = (store / "entities.jsonl").read_text(encoding="utf-8")
    relations_jsonl = (store / "relations.jsonl").read_text(encoding="utf-8")
    assert "diabetes" not in entities_jsonl.lower()
    assert "ByteDance" not in entities_jsonl
    assert deleted not in relations_jsonl

    reloaded = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    graph = reloaded.graph("u")
    rendered = str(graph)
    assert "Moonshot AI" in rendered
    assert "ByteDance" not in rendered
    assert "diabetes" not in rendered.lower()
    assert len([edge for edge in graph["edges"] if edge["fact_id"] == edited]) == 1


def test_service_profile_fact_list_includes_cold_facts(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    cold = mem.add_fact("project", "project_note", "cold-profile-value", user_id="u")
    hot = mem.add_fact("project", "project_note_2", "hot-profile-value", user_id="u")
    cold.salience = 0.0
    hot.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1

    facts = svc.profile("u")["facts"]
    assert any("cold-profile-value" in f for f in facts)
    assert any("hot-profile-value" in f for f in facts)


def test_service_remember_total_facts_counts_cold_tier(tmp_path):
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing")
    mem = svc.get("u")
    for i in range(5):
        f = mem.add_fact("project", f"project_note_{i}", f"value-{i}", user_id="u")
        f.salience = float(i)
    assert mem.evict_cold(max_hot=1) == 4

    out = svc.remember("u", "Just saying hello.", scope="long")
    assert out["ok"] is True
    assert out["total_facts"] >= 5
