"""Entity-term index behind query anchoring.

`query_entity_ids` walked every entity in the store on every retrieval to find which ones the query
names. The graph now indexes entity name and alias terms, so only the query's own terms are looked up.

The load-bearing test is `test_indexed_and_scanned_anchoring_agree`: it runs the real retriever against a
graph store with the index and against one with the lookup removed, and requires identical answers. The
index is only allowed to be faster, never different.
"""
from __future__ import annotations

from engram.config import Config
from engram.embed.hashing import HashingEmbedder
from engram.retrieve.hybrid import HybridRetriever
from engram.store.memory_store import InMemoryGraphStore, InMemoryVectorStore
from engram.types import Entity

NAMES = [
    "Lisbon", "Berlin", "Acme Corp", "Initech", "Kyoto University",
    "oat milk", "road bike", "Zephyr", "Wei", "the Berlin office",
]

QUERIES = [
    "where does wei work",
    "tell me about lisbon",
    "did wei visit kyoto university",
    "what happened at acme corp",
    "anything about zephyr",
    "somewhere not lisbon",
    "does she prefer oat milk",
    "berlin",
    "nothing relevant here at all",
    "the berlin office and initech",
]


class UnindexedGraphStore(InMemoryGraphStore):
    """The same store with the term lookup hidden, so the retriever takes its full-scan path."""

    entities_by_terms = None  # type: ignore[assignment]


def _populate(graph: InMemoryGraphStore, user_id: str = "u1") -> InMemoryGraphStore:
    for name in NAMES:
        graph.upsert_entity(Entity(user_id=user_id, name=name))
    graph.upsert_entity(Entity(user_id="other", name="Lisbon"))  # a second tenant, must stay invisible
    return graph


def _retriever(graph: InMemoryGraphStore) -> HybridRetriever:
    return HybridRetriever(InMemoryVectorStore(), graph, HashingEmbedder(), Config())


def test_indexed_and_scanned_anchoring_agree():
    """The property that makes the index safe: same answers, whichever path ran."""
    indexed = _retriever(_populate(InMemoryGraphStore()))
    scanned = _retriever(_populate(UnindexedGraphStore()))

    # Ids differ between the two stores, so compare the entity NAMES each path anchored on.
    def names(retriever, query):
        return sorted(
            retriever.graph.entities[eid].name for eid in retriever.query_entity_ids(query, "u1")
        )

    for query in QUERIES:
        assert names(indexed, query) == names(scanned, query), f"paths disagree on {query!r}"


def test_lookup_only_touches_requested_terms():
    graph = _populate(InMemoryGraphStore())
    hits = graph.entities_by_terms("u1", {"lisbon", "berlin", "absent"})
    assert set(hits) == {"lisbon", "berlin"}
    assert [e.name for e in hits["lisbon"]] == ["Lisbon"]
    assert {e.name for e in hits["berlin"]} == {"Berlin", "the Berlin office"}


def test_lookup_is_tenant_scoped():
    """Another tenant's identically named entity must not surface."""
    graph = _populate(InMemoryGraphStore())
    hits = graph.entities_by_terms("u1", {"lisbon"})
    assert all(e.user_id == "u1" for e in hits["lisbon"])
    assert graph.entities_by_terms("nobody", {"lisbon"}) == {}


def test_multi_word_names_are_indexed_by_every_term():
    graph = _populate(InMemoryGraphStore())
    for term in ("kyoto", "university"):  # every token of a multi-word name is a lookup key
        hits = graph.entities_by_terms("u1", {term})
        assert any(e.name == "Kyoto University" for e in hits.get(term, [])), term


def test_aliases_are_indexed():
    graph = InMemoryGraphStore()
    graph.upsert_entity(Entity(user_id="u1", name="Wei", aliases=["Xiaowei", "老王"]))
    hits = graph.entities_by_terms("u1", {"xiaowei"})
    assert [e.name for e in hits["xiaowei"]] == ["Wei"]


def test_pruning_an_orphan_removes_it_from_the_index():
    """A stale posting would resurrect a deleted entity as a query anchor."""
    graph = _populate(InMemoryGraphStore())
    assert graph.entities_by_terms("u1", {"lisbon"})
    assert graph.prune_orphan_entities() > 0  # no relations exist, so every entity is an orphan
    assert graph.entities_by_terms("u1", {"lisbon"}) == {}


def test_reupserting_a_name_does_not_duplicate_postings():
    graph = InMemoryGraphStore()
    first = graph.upsert_entity(Entity(user_id="u1", name="Lisbon"))
    again = graph.upsert_entity(Entity(user_id="u1", name="Lisbon"))
    assert again.id == first.id
    assert [e.id for e in graph.entities_by_terms("u1", {"lisbon"})["lisbon"]] == [first.id]
