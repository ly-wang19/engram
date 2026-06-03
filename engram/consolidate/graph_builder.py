"""Project atomic Facts into the bi-temporal knowledge graph (entities + time-stamped relation edges)."""
from __future__ import annotations

from ..embed import Embedder
from ..store import GraphStore
from ..types import Entity, Fact, Relation


class GraphBuilder:
    def __init__(self, graph: GraphStore, embedder: Embedder) -> None:
        self.graph = graph
        self.embedder = embedder

    def add_fact(self, fact: Fact) -> None:
        subj = self.graph.upsert_entity(Entity(name=fact.subject, user_id=fact.user_id))
        obj = self.graph.upsert_entity(Entity(name=fact.object, user_id=fact.user_id))
        self.graph.add_relation(
            Relation(
                subject_id=subj.id,
                predicate=fact.predicate,
                object_id=obj.id,
                fact_id=fact.id,
                valid_at=fact.valid_at,
                invalid_at=fact.invalid_at,
            )
        )

    def invalidate(self, fact_id: str, t: float) -> None:
        self.graph.invalidate_relations_for_fact(fact_id, t)
