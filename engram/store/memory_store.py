"""In-memory reference stores. Brute-force and unindexed -- correct and dependency-free, not fast.
Good to ~10k items, which is plenty for the demo, tests, and small eval slices."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from ..types import Entity, Relation, is_visible
from ..util import cosine
from .base import DocStore, GraphStore, Predicate, VectorStore


class InMemoryVectorStore(VectorStore):
    def __init__(self) -> None:
        self._d: dict[str, tuple[list[float], Any]] = {}

    def upsert(self, key: str, vector: list[float], payload: Any) -> None:
        self._d[key] = (vector, payload)

    def search(
        self, vector: list[float], top_k: int, where: Optional[Predicate] = None
    ) -> list[tuple[float, Any]]:
        scored = [
            (cosine(vector, vec), payload)
            for vec, payload in self._d.values()
            if where is None or where(payload)
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


class InMemoryGraphStore(GraphStore):
    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self._by_name: dict[tuple[str, str], str] = {}
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
        return entity

    def get_entity(self, user_id: str, name: str) -> Entity | None:
        eid = self._by_name.get((user_id, name.lower()))
        return self.entities.get(eid) if eid else None

    def add_relation(self, relation: Relation) -> None:
        self.rels[relation.id] = relation
        self._out[relation.subject_id].append(relation.id)
        self._in[relation.object_id].append(relation.id)

    def neighbors(
        self,
        entity_id: str,
        as_of: Optional[float] = None,
        direction: str = "out",
        known_at: Optional[float] = None,
    ) -> list[Relation]:
        rel_ids = self._out[entity_id] if direction == "out" else self._in[entity_id]
        out: list[Relation] = []
        for rid in rel_ids:
            r = self.rels[rid]
            if is_visible(r, as_of=as_of, known_at=known_at):
                out.append(r)
        return out

    def invalidate_relations_for_fact(
        self,
        fact_id: str,
        valid_at: float,
        expired_at: Optional[float] = None,
    ) -> None:
        transaction_end = valid_at if expired_at is None else expired_at
        for r in self.rels.values():
            if r.fact_id != fact_id:
                continue
            if r.invalid_at is None:
                r.invalid_at = valid_at
            if r.expired_at is None:
                r.expired_at = transaction_end

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
            self._out.pop(eid, None)
            self._in.pop(eid, None)
            removed += 1
        return removed

    def relations(self) -> list[Relation]:
        return list(self.rels.values())
