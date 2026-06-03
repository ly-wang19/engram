"""System-1: the hot write path. No LLM on the critical path -- we append the lossless episode, resolve
identity, compute a light embedding, and hand off to async consolidation. Target: sub-50ms perceived."""
from __future__ import annotations

from typing import Optional

from ..embed import Embedder
from ..store import DocStore, VectorStore
from ..types import Episode
from ..util import now
from .identity import IdentityResolver


class Ingestor:
    def __init__(
        self,
        episodes_doc: DocStore,
        episodes_vec: VectorStore,
        embedder: Embedder,
        resolver: Optional[IdentityResolver] = None,
    ) -> None:
        self.episodes_doc = episodes_doc
        self.episodes_vec = episodes_vec
        self.embedder = embedder
        self.resolver = resolver or IdentityResolver()

    def ingest(
        self,
        content: str,
        user_id: str = "default",
        session_id: str = "default",
        speaker: str = "user",
        event_time: Optional[float] = None,
        embedding: Optional[list] = None,
    ) -> Episode:
        canonical = self.resolver.resolve(user_id)
        ep = Episode(
            content=content,
            user_id=canonical,
            session_id=session_id,
            speaker=speaker,
            event_time=event_time if event_time is not None else now(),
        )
        # Accept a precomputed embedding so callers can batch-embed many episodes in one model.encode
        # call (10x+ faster than per-episode embed when ingesting a whole session haystack at once).
        ep.embedding = embedding if embedding is not None else self.embedder.embed(content)
        # append-only log + episodic vector index; consolidated=False enqueues it for System-2.
        self.episodes_doc.put(ep.id, ep)
        self.episodes_vec.upsert(ep.id, ep.embedding, ep)
        return ep

    def pending(self) -> list[Episode]:
        """Episodes not yet consolidated (the System-2 work queue)."""
        return [ep for ep in self.episodes_doc.values() if not ep.consolidated]
