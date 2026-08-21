"""In-memory reference stores. Brute-force and unindexed -- correct and dependency-free, not fast.
Good to ~10k items, which is plenty for the demo, tests, and small eval slices."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Optional

from ..types import Entity, Relation
from ..util import cosine, stem, tokenize
from .base import DocStore, GraphStore, Predicate, VectorStore


class InMemoryVectorStore(VectorStore):
    def __init__(self) -> None:
        self._d: dict[str, tuple[list[float], Any]] = {}

    def upsert(self, key: str, vector: list[float], payload: Any) -> None:
        self._d[key] = (vector, payload)

    def search(
        self,
        vector: list[float],
        top_k: int,
        where: Optional[Predicate] = None,
        *,
        user_id: Optional[str] = None,
    ) -> list[tuple[float, Any]]:
        # No index to push a tenant filter into, so it is just another equality check here. The reference
        # store is brute-force by design (see the module docstring); scale comes from a real backend.
        scored = [
            (cosine(vector, vec), payload)
            for vec, payload in self._d.values()
            if (user_id is None or getattr(payload, "user_id", None) == user_id)
            and (where is None or where(payload))
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    def get(self, key: str) -> Any | None:
        hit = self._d.get(key)
        return hit[1] if hit else None

    def delete(self, key: str) -> None:
        self._d.pop(key, None)

    def values(self) -> list[Any]:
        return [payload for _, payload in self._d.values()]


class InMemoryDocStore(DocStore):
    def __init__(self) -> None:
        self._d: dict[str, Any] = {}

    def put(self, key: str, obj: Any) -> None:
        self._d[key] = obj

    def get(self, key: str) -> Any | None:
        return self._d.get(key)

    def values(self) -> list[Any]:
        return list(self._d.values())

    def delete(self, key: str) -> None:
        self._d.pop(key, None)


def _name_terms(entity: Entity) -> set[str]:
    """Stemmed tokens of an entity's name and aliases — the keys it can be looked up by."""
    terms: set[str] = set()
    for text in (entity.name, *entity.aliases):
        for token in tokenize(text):
            terms.add(stem(token))
    return terms


class InMemoryGraphStore(GraphStore):
    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self._by_name: dict[tuple[str, str], str] = {}
        # (user_id, stemmed term) -> entity ids. Anchoring a query to its entities otherwise means
        # walking every entity in the store on every retrieval; this turns it into a lookup of the
        # query's own terms. Built at upsert: an entity's name and aliases are fixed once inserted
        # (upsert_entity returns the existing node rather than updating it), so there is nothing to
        # invalidate. A backend that lets names change would need to re-index on that change.
        self._by_term: dict[tuple[str, str], set[str]] = {}
        self.rels: dict[str, Relation] = {}
        self._out: dict[str, list[str]] = defaultdict(list)
        self._in: dict[str, list[str]] = defaultdict(list)

    def upsert_entity(self, entity: Entity) -> Entity:
        key = (entity.user_id, entity.name.lower())
        existing_id = self._by_name.get(key)
        if existing_id is not None:
            return self.entities[existing_id]
        self.entities[entity.id] = entity
        self._by_name[key] = entity.id
        for term in _name_terms(entity):
            self._by_term.setdefault((entity.user_id, term), set()).add(entity.id)
        return entity

    def entities_by_terms(self, user_id: str, terms: Iterable[str]) -> dict[str, list[Entity]]:
        """For each requested term, this user's entities whose name or aliases contain it.

        Serves both halves of query anchoring: the union across terms is the candidate set to test
        properly, and a single term's list size is the uniqueness signal an alias anchor needs. Only the
        query's terms are looked up, so the cost follows the query rather than the store.
        """
        found: dict[str, list[Entity]] = {}
        for term in terms:
            ids = self._by_term.get((user_id, term))
            if ids:
                found[term] = [self.entities[eid] for eid in ids if eid in self.entities]
        return found

    def get_entity(self, user_id: str, name: str) -> Entity | None:
        eid = self._by_name.get((user_id, name.lower()))
        return self.entities.get(eid) if eid else None

    def add_relation(self, relation: Relation) -> None:
        self.rels[relation.id] = relation
        self._out[relation.subject_id].append(relation.id)
        self._in[relation.object_id].append(relation.id)

    def neighbors(
        self, entity_id: str, as_of: Optional[float] = None, direction: str = "out"
    ) -> list[Relation]:
        rel_ids = self._out[entity_id] if direction == "out" else self._in[entity_id]
        out: list[Relation] = []
        for rid in rel_ids:
            r = self.rels[rid]
            live = as_of is None or (
                r.valid_at <= as_of and (r.invalid_at is None or r.invalid_at > as_of)
            )
            if live:
                out.append(r)
        return out

    def invalidate_relations_for_fact(self, fact_id: str, t: float) -> None:
        for r in self.rels.values():
            if r.fact_id == fact_id and r.invalid_at is None:
                r.invalid_at = t

    def delete_relations_for_fact(self, fact_id: str) -> None:
        for rid, rel in list(self.rels.items()):
            if rel.fact_id != fact_id:
                continue
            self.rels.pop(rid, None)
            if rid in self._out.get(rel.subject_id, []):
                self._out[rel.subject_id].remove(rid)
            if rid in self._in.get(rel.object_id, []):
                self._in[rel.object_id].remove(rid)

    def prune_orphan_entities(self) -> int:
        referenced = {r.subject_id for r in self.rels.values()} | {r.object_id for r in self.rels.values()}
        removed = 0
        for eid, ent in list(self.entities.items()):
            if eid in referenced:
                continue
            self.entities.pop(eid, None)
            self._by_name.pop((ent.user_id, ent.name.lower()), None)
            for term in _name_terms(ent):
                key = (ent.user_id, term)
                ids = self._by_term.get(key)
                if ids is not None:
                    ids.discard(eid)
                    if not ids:
                        del self._by_term[key]
            self._out.pop(eid, None)
            self._in.pop(eid, None)
            removed += 1
        return removed

    def relations(self) -> list[Relation]:
        return list(self.rels.values())
