"""Project atomic Facts into the bi-temporal knowledge graph (entities + time-stamped relation edges)."""
from __future__ import annotations

from ..embed import Embedder
from ..store import GraphStore
from ..types import Entity, Fact, Relation
from .outcomes import OUTCOME_PREDICATES

# Predicates whose object is prose, not a nameable thing. An edge here would upsert the whole sentence as
# an entity. Session outcomes are the worst case of that: the object is a full conclusion and the subject
# is a session id, so every distilled statement would manufacture one junk node plus one node per session,
# poisoning /v1/graph and inventing orphan_entity findings in the very audit meant to clean the store up.
_TEXTUAL_OBJECT_PREDICATES = {
    "procedure", "how_to", "routine", "instruction", "agent_instruction",
    *OUTCOME_PREDICATES,
}


class GraphBuilder:
    def __init__(self, graph: GraphStore, embedder: Embedder) -> None:
        self.graph = graph
        self.embedder = embedder

    def add_fact(self, fact: Fact) -> None:
        if fact.predicate.lower() in _TEXTUAL_OBJECT_PREDICATES:
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
