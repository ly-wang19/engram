"""Multi-hop planner tests (CLAUDE.md Bet B). Exercise the new LLM-driven decomposition and its
fallbacks against a tiny hand-built graph, so they're deterministic and need no real model.

The chain under test: Wei -colleague-> Lei -works_at-> Moonshot. A two-hop question ("where does Wei's
colleague work?") must resolve to Moonshot. We cover: the LLM plan walking the graph, invented predicates
being dropped, malformed output falling back to the keyword decomposer, the fact-store bridge recovering a
missing edge, and the offline path staying byte-for-byte (no bridge, no LLM)."""
from __future__ import annotations

from engram.config import Config
from engram.llm.fake import FakeLLM
from engram.retrieve.planner import MultiHopPlanner
from engram.store.memory_store import InMemoryGraphStore, InMemoryVectorStore
from engram.types import Entity, Fact, Relation

_Q = "Where does Wei's colleague work?"  # stems to wei/colleague/work -> anchors + keyword-decomposes


def _build(bridge_last: bool = False):
    """Wei -colleague-> Lei -works_at-> Moonshot. With bridge_last=True the works_at hop exists ONLY as a
    Fact (no graph edge), so the planner must fall back to the fact-store bridge to complete the chain."""
    g = InMemoryGraphStore()
    fs = InMemoryVectorStore()
    ents = {name: g.upsert_entity(Entity(name=name, user_id="u")) for name in ("Wei", "Lei", "Moonshot")}

    def add(subj, pred, obj, edge=True):
        f = Fact(subject=subj, predicate=pred, object=obj, user_id="u")
        fs.upsert(f.id, [0.0], f)  # planner never embeds; a dummy vector is fine
        if edge:
            g.add_relation(Relation(subject_id=ents[subj].id, predicate=pred,
                                    object_id=ents[obj].id, fact_id=f.id))
        return f

    add("Wei", "colleague", "Lei")
    add("Lei", "works_at", "Moonshot", edge=not bridge_last)
    return g, fs


def _planner(g, fs, llm=None):
    return MultiHopPlanner(g, fs, Config(), llm=llm)


def test_llm_plan_walks_the_graph():
    g, fs = _build()
    llm = FakeLLM(responses=['{"anchor":"Wei","predicates":["colleague","works_at"]}'])
    res = _planner(g, fs, llm).plan(_Q, "u")
    assert res is not None
    assert res.answer == "Moonshot"
    assert res.chain == ["colleague", "works_at"]
    assert len(res.facts) >= 2  # both hops' evidence collected


def test_invented_predicate_is_dropped_then_keyword_fallback_recovers():
    g, fs = _build()
    # 'bogus_pred' isn't in the user's vocabulary -> filtered out -> only 1 valid pred -> LLM plan rejected
    # -> the deterministic keyword decomposer takes over and still resolves the chain.
    llm = FakeLLM(responses=['{"anchor":"Wei","predicates":["colleague","bogus_pred"]}'])
    res = _planner(g, fs, llm).plan(_Q, "u")
    assert res is not None and res.answer == "Moonshot"


def test_malformed_llm_output_falls_back_to_keywords():
    g, fs = _build()
    res = _planner(g, fs, FakeLLM(responses=["I cannot help with that."])).plan(_Q, "u")
    assert res is not None and res.answer == "Moonshot"


def test_bridge_recovers_a_missing_graph_edge():
    g, fs = _build(bridge_last=True)  # works_at exists only as a Fact, not a graph edge
    llm = FakeLLM(responses=['{"anchor":"Wei","predicates":["colleague","works_at"]}'])
    res = _planner(g, fs, llm).plan(_Q, "u")
    assert res is not None and res.answer == "Moonshot"
    assert res.chain == ["colleague", "works_at"]


def test_offline_path_is_unchanged_no_bridge():
    # llm=None: keyword decomposer, bridge gated OFF. A missing edge means no confident multi-hop answer,
    # so plan() returns None and search() falls through to hybrid (the pre-existing behavior).
    g, fs = _build(bridge_last=True)
    assert _planner(g, fs, None).plan(_Q, "u") is None


def test_offline_path_resolves_when_edges_present():
    g, fs = _build()
    res = _planner(g, fs, None).plan(_Q, "u")
    assert res is not None and res.answer == "Moonshot"


def test_single_hop_question_is_not_planned():
    g, fs = _build()
    # one predicate -> not multi-hop -> None (hybrid handles it)
    assert _planner(g, fs, None).plan("Where does Wei live?", "u") is None


def test_unknown_anchor_returns_none():
    g, fs = _build()
    # LLM names an anchor that isn't in the graph; the query has no entity token either -> no anchor -> None
    llm = FakeLLM(responses=['{"anchor":"Nobody","predicates":["colleague","works_at"]}'])
    assert _planner(g, fs, llm).plan("Where does the colleague work and live?", "u") is None
