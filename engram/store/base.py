"""Storage interfaces. The reference implementations are in-memory (zero setup); production swaps in
LanceDB/Qdrant/pgvector (vector), Kuzu/Neo4j (graph), and a real doc store behind these same APIs."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from ..types import Entity, Relation

Predicate = Callable[[Any], bool]


class VectorStore(ABC):
    """Maps a key -> (vector, payload) and does nearest-neighbour search with an optional filter."""

    @abstractmethod
    def upsert(self, key: str, vector: list[float], payload: Any) -> None: ...

    @abstractmethod
    def search(
        self, vector: list[float], top_k: int, where: Optional[Predicate] = None
    ) -> list[tuple[float, Any]]: ...

    @abstractmethod
    def get(self, key: str) -> Any | None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def values(self) -> list[Any]: ...


class DocStore(ABC):
    """A plain key->object store (used for the append-only Episode log)."""

    @abstractmethod
    def put(self, key: str, obj: Any) -> None: ...

    @abstractmethod
    def get(self, key: str) -> Any | None: ...

    @abstractmethod
    def values(self) -> list[Any]: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...


class GraphStore(ABC):
    """A bi-temporal property graph: entity nodes + time-stamped relation edges."""

    @abstractmethod
    def upsert_entity(self, entity: Entity) -> Entity:
        """Resolve by (user_id, name); return the existing node if present, else insert."""

    @abstractmethod
    def get_entity(self, user_id: str, name: str) -> Entity | None: ...

    @abstractmethod
    def add_relation(self, relation: Relation) -> None: ...

    @abstractmethod
    def neighbors(
        self,
        entity_id: str,
        as_of: Optional[float] = None,
        direction: str = "out",
        known_at: Optional[float] = None,
    ) -> list[Relation]:
        """Relations incident to ``entity_id`` visible in the requested temporal view."""

    @abstractmethod
    def invalidate_relations_for_fact(
        self,
        fact_id: str,
        valid_at: float,
        expired_at: Optional[float] = None,
    ) -> None:
        """Close an edge's world-valid and transaction-known intervals independently."""

    @abstractmethod
    def delete_relations_for_fact(self, fact_id: str) -> None:
        """Hard-remove all graph edges projected from a fact. Use only for user edits/erasure."""

    @abstractmethod
    def prune_orphan_entities(self) -> int:
        """Remove entities that are no longer referenced by any relation. Returns removed count."""

    @abstractmethod
    def relations(self) -> list[Relation]: ...
