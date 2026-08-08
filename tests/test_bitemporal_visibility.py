"""Regression coverage for the two independent bi-temporal visibility axes."""
from __future__ import annotations

from engram import Memory
from engram.consolidate.graph_builder import GraphBuilder
from engram.embed import HashingEmbedder
from engram.retrieve.temporal import live_at, visible_at
from engram.store import InMemoryGraphStore
from engram.types import Fact, Relation


def test_fact_valid_and_transaction_intervals_are_independent() -> None:
    fact = Fact(
        "user",
        "works_at",
        "Acme",
        valid_at=10.0,
        invalid_at=30.0,
        created_at=20.0,
        expired_at=40.0,
    )

    assert not fact.is_valid_at(9.0)
    assert fact.is_valid_at(10.0)
    assert fact.is_valid_at(29.0)
    assert not fact.is_valid_at(30.0)

    assert not fact.is_known_at(19.0)
    assert fact.is_known_at(20.0)
    assert fact.is_known_at(39.0)
    assert not fact.is_known_at(40.0)

    assert fact.is_visible_at(valid_time=15.0, transaction_time=25.0)
    assert not fact.is_visible_at(valid_time=35.0, transaction_time=25.0)
    assert not fact.is_visible_at(valid_time=15.0, transaction_time=15.0)


def test_is_live_as_of_does_not_reveal_a_fact_before_it_was_learned() -> None:
    fact = Fact(
        "user",
        "lives_in",
        "Shanghai",
        valid_at=10.0,
        created_at=20.0,
    )

    assert not fact.is_live(15.0)
    assert fact.is_live(20.0)
    assert live_at([fact], 15.0) == []
    assert live_at([fact], 20.0) == [fact]


def test_visible_at_can_query_valid_and_transaction_time_separately() -> None:
    learned_late = Fact(
        "user",
        "project_status",
        "started",
        valid_at=10.0,
        created_at=20.0,
    )

    assert visible_at(
        [learned_late],
        valid_time=10.0,
        transaction_time=19.0,
    ) == []
    assert visible_at(
        [learned_late],
        valid_time=10.0,
        transaction_time=20.0,
    ) == [learned_late]


def test_relation_has_the_same_bitemporal_visibility_semantics() -> None:
    relation = Relation(
        subject_id="en_subject",
        predicate="works_at",
        object_id="en_object",
        fact_id="ft_source",
        valid_at=10.0,
        invalid_at=30.0,
        created_at=20.0,
        expired_at=40.0,
    )

    assert not relation.is_live(15.0)
    assert relation.is_live(20.0)
    assert relation.is_visible_at(valid_time=25.0, transaction_time=25.0)
    assert not relation.is_visible_at(valid_time=30.0, transaction_time=25.0)
    assert not relation.is_visible_at(valid_time=25.0, transaction_time=40.0)
    assert visible_at([relation], valid_time=25.0, transaction_time=25.0) == [relation]


def test_relation_existing_positional_id_argument_remains_compatible() -> None:
    relation = Relation("en_subject", "knows", "en_object", "ft_source", 10.0, None, "rel_fixed")

    assert relation.id == "rel_fixed"


def test_public_as_of_remains_valid_time_only_and_known_at_enables_bitemporal_view() -> None:
    mem = Memory()
    fact = Fact(
        "user",
        "works_at",
        "Acme",
        user_id="user",
        valid_at=10.0,
        created_at=20.0,
        embedding=mem.embedder.embed("user works at Acme"),
    )
    mem.fact_store.upsert(fact.id, fact.embedding or [], fact)
    mem.engine.graph_builder.add_fact(fact)

    assert mem.search("Where does the user work?", user_id="user", as_of=15.0).answer() == "Acme"
    assert mem.search(
        "Where does the user work?",
        user_id="user",
        as_of=15.0,
        known_at=15.0,
    ).abstained
    assert mem.search(
        "Where does the user work?",
        user_id="user",
        as_of=15.0,
        known_at=20.0,
    ).answer() == "Acme"
    assert mem.search(
        "Where does the user work?",
        user_id="user",
        known_at=15.0,
    ).abstained


def test_episode_reads_obey_ingestion_time_only_when_known_at_is_supplied() -> None:
    mem = Memory()
    episode = mem.add("Project Atlas uses Python.", user_id="user", event_time=10.0)
    episode.ingested_at = 20.0

    assert mem.retrieve_episodes("Atlas", user_id="user", as_of=15.0) == [episode]
    assert mem.retrieve_episodes(
        "Atlas",
        user_id="user",
        as_of=15.0,
        known_at=15.0,
    ) == []
    assert mem.retrieve_episodes(
        "Atlas",
        user_id="user",
        as_of=15.0,
        known_at=20.0,
    ) == [episode]


def test_graph_projection_and_invalidation_keep_both_time_axes_separate() -> None:
    graph = InMemoryGraphStore()
    builder = GraphBuilder(graph, HashingEmbedder())
    fact = Fact(
        "user",
        "works_at",
        "Acme",
        user_id="user",
        valid_at=10.0,
        created_at=20.0,
    )
    builder.add_fact(fact)
    relation = graph.relations()[0]

    assert relation.valid_at == 10.0
    assert relation.created_at == 20.0
    assert graph.neighbors(relation.subject_id, as_of=15.0, direction="out") == [relation]
    assert graph.neighbors(
        relation.subject_id,
        as_of=15.0,
        direction="out",
        known_at=15.0,
    ) == []

    builder.invalidate(fact.id, valid_at=30.0, expired_at=40.0)

    assert relation.invalid_at == 30.0
    assert relation.expired_at == 40.0
    assert graph.neighbors(relation.subject_id, as_of=29.0, direction="out") == [relation]
    assert graph.neighbors(relation.subject_id, as_of=30.0, direction="out") == []
