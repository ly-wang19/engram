"""Project atomic Facts into the bi-temporal knowledge graph (entities + time-stamped relation edges)."""
from __future__ import annotations

from ..embed import Embedder
from ..util import entity_worthy
from ..store import GraphStore
from ..types import Entity, Fact, Relation

_TEXTUAL_OBJECT_PREDICATES = {"procedure", "how_to", "routine", "instruction", "agent_instruction"}


class GraphBuilder:
    def __init__(self, graph: GraphStore, embedder: Embedder) -> None:
        self.graph = graph
        self.embedder = embedder

    def add_fact(self, fact: Fact) -> None:
        if fact.predicate.lower() in _TEXTUAL_OBJECT_PREDICATES:
            return
        # Sentence-length or symbol-noise strings can't be graph nodes: nothing else will ever
        # reference the same surface form, so they'd sit as permanent orphans that n-hop walks
        # can't traverse. The fact itself still lands in the vector/BM25 stores untouched.
        if not (entity_worthy(fact.subject) and entity_worthy(fact.object)):
            return
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
