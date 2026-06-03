"""System-2 orchestration: drain pending episodes -> extract -> reconcile conflicts -> store facts +
build the bi-temporal graph. Runs off the critical path (async / sleep-time in production)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from ..config import Config
from ..embed import Embedder
from ..llm import LLM
from ..store import GraphStore, VectorStore
from ..types import Episode
from .conflict import ConflictResolver
from .extractor import RuleExtractor
from .graph_builder import GraphBuilder
from .summarizer import ProfileBuilder


class ConsolidationEngine:
    def __init__(
        self,
        fact_store: VectorStore,
        graph: GraphStore,
        embedder: Embedder,
        config: Config,
        llm: Optional[LLM] = None,
    ) -> None:
        self.fact_store = fact_store
        self.graph = graph
        self.embedder = embedder
        self.config = config
        self.llm = llm
        if llm is not None:
            from .llm_extractor import LLMExtractor

            self.extractor = LLMExtractor(llm)
        else:
            self.extractor = RuleExtractor()
        self.graph_builder = GraphBuilder(graph, embedder)
        # Semantic conflict detection needs a real (semantic) embedder; the offline HashingEmbedder
        # produces meaningless cosines, so we gate it off there → exact-slot only, fully deterministic.
        from ..embed import HashingEmbedder

        sem_embedder = None if isinstance(embedder, HashingEmbedder) else embedder
        self.conflict = ConflictResolver(sem_embedder, config.conflict_sim_threshold)
        self.profiles = ProfileBuilder()

    def consolidate(self, episodes: list[Episode]) -> dict[str, int]:
        stats = {"facts_added": 0, "duplicates": 0, "invalidated": 0}
        chrono = sorted(episodes, key=lambda e: (e.event_time, e.ingested_at))

        # Step 1: extract facts from all episodes in parallel — each extraction is an independent LLM
        # call (no shared state). Cap concurrency at 4: with multiple bench workers each calling this,
        # an 8-wide pool meant ~24 simultaneous relay calls → throttling/backoff that was SLOWER overall.
        # 4 keeps the relay healthy while still collapsing 8 serial waits into ~2 rounds.
        ep_facts: dict[str, list] = {}
        if len(chrono) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(chrono))) as pool:
                futs = {pool.submit(self.extractor.extract, ep): ep for ep in chrono}
                for fut in as_completed(futs):
                    ep = futs[fut]
                    try:
                        ep_facts[ep.id] = fut.result()
                    except Exception:  # noqa: BLE001
                        ep_facts[ep.id] = []  # extraction failure → no facts from this episode
        else:
            for ep in chrono:
                ep_facts[ep.id] = self.extractor.extract(ep)

        # Step 2: reconcile conflicts in chronological order — ordering matters for "supersedes" chains.
        for ep in chrono:
            for fact in ep_facts.get(ep.id, []):
                fact.embedding = self.embedder.embed(fact.text)
                live = [f for f in self.fact_store.values() if f.user_id == fact.user_id and f.is_live()]
                action, invalidated = self.conflict.reconcile(fact, live)
                for old in invalidated:
                    self.graph_builder.invalidate(old.id, fact.created_at)
                    stats["invalidated"] += 1
                if action == "duplicate":
                    stats["duplicates"] += 1
                    continue
                self.fact_store.upsert(fact.id, fact.embedding, fact)
                self.graph_builder.add_fact(fact)
                stats["facts_added"] += 1
            ep.consolidated = True
        return stats

    def self_name(self, user_id: str) -> str:
        return self.extractor.self_of(user_id)

    def profile(self, user_id: str) -> dict[str, str]:
        subject = self.self_name(user_id)
        live = [f for f in self.fact_store.values() if f.user_id == user_id and f.is_live()]
        return self.profiles.build(subject, live)
