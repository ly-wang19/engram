"""Graph entity normalization (Bet B hygiene): sentence/noise strings never mint graph nodes, and
surface variants of one name converge on ONE node with the variants kept as aliases — scattered
duplicates and permanent orphans starve graph proximity and n-hop walks."""
from __future__ import annotations

from engram.memory import Memory
from engram.types import Entity, Fact, Relation
from engram.util import canon_entity_name, entity_worthy


class TestHelpers:
    def test_canon_unifies_surface_variants_only(self):
        assert canon_entity_name("Engram-Memory") == canon_entity_name("engram memory") \
            == canon_entity_name("Engram_Memory")
        # semantic aliasing is deliberately NOT attempted
        assert canon_entity_name("Engram") != canon_entity_name("开源记忆引擎")

    def test_worthiness_rejects_real_world_noise(self):
        # actual offenders observed in a live graph
        assert not entity_worthy("✓ Connected")
        assert not entity_worthy("在低 token 和低延迟的中立可复现测试平台上取得最高准确率，并击败全上下文基准")
        assert not entity_worthy("AI项目经理落地（首个落地场景：运营期每日班前会）")
        assert not entity_worthy("")

    def test_worthiness_keeps_legit_names(self):
        for name in ("user", "腾讯混元", "Moonshot AI", "《AI项目经理资料库》", "dws CLI"):
            assert entity_worthy(name), name


class TestGraphStore:
    def test_variants_fold_into_one_node_with_aliases(self):
        m = Memory()
        g = m.graph
        a = g.upsert_entity(Entity(name="Engram-Memory", user_id="u"))
        b = g.upsert_entity(Entity(name="engram memory", user_id="u"))
        assert a.id == b.id
        assert "engram memory" in a.aliases
        # lookup works under any variant spelling
        assert g.get_entity("u", "Engram_Memory").id == a.id

    def test_sentence_facts_do_not_mint_nodes(self):
        m = Memory()
        f = Fact(subject="user", predicate="goal",
                 object="在低 token 和低延迟的中立可复现测试平台上取得最高准确率，并击败全上下文基准",
                 user_id="u")
        f.embedding = m.embedder.embed(f.text)
        m.engine.graph_builder.add_fact(f)
        assert m.graph.get_entity("u", f.object) is None
        assert m.graph.get_entity("u", "user") is None  # whole edge skipped, no orphan subject

    def test_normal_facts_still_build_edges(self):
        m = Memory()
        f = Fact(subject="user", predicate="works_at", object="Moonshot AI", user_id="u")
        f.embedding = m.embedder.embed(f.text)
        m.engine.graph_builder.add_fact(f)
        subj = m.graph.get_entity("u", "user")
        assert subj is not None and len(m.graph.neighbors(subj.id)) == 1


class TestPersistRemap:
    def test_folded_entities_remap_relations_on_load(self, tmp_path):
        # simulate a pre-normalization store: two separator-variant entities + an edge on EACH id
        from engram.store import persist
        m = Memory()
        e1 = Entity(name="Engram-Memory", user_id="u")
        e2 = Entity(name="engram memory", user_id="u")
        target = Entity(name="LongMemEval", user_id="u")
        m.graph.entities = {}  # bypass upsert so both variants exist like an old snapshot
        for e in (e1, e2, target):
            m.graph.entities[e.id] = e
            m.graph._by_name[(e.user_id, e.name.lower())] = e.id
        m.graph.add_relation(Relation(subject_id=e1.id, predicate="evaluated_on",
                                      object_id=target.id, fact_id="f1", valid_at=1.0))
        m.graph.add_relation(Relation(subject_id=e2.id, predicate="reported_on",
                                      object_id=target.id, fact_id="f2", valid_at=2.0))
        persist.save_memory(m, str(tmp_path))
        m2 = Memory()
        persist.load_memory(m2, str(tmp_path))
        node = m2.graph.get_entity("u", "engram_memory")
        assert node is not None
        # both edges converge on the single folded node; none dangles
        assert len(m2.graph.neighbors(node.id)) == 2
        live_ids = set(m2.graph.entities)
        for r in m2.graph.relations():
            assert r.subject_id in live_ids and r.object_id in live_ids
