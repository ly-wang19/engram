"""System-2 orchestration: drain pending episodes -> extract -> reconcile conflicts -> store facts +
build the bi-temporal graph. Runs off the critical path (async / sleep-time in production)."""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from ..config import Config
from ..embed import Embedder
from ..llm import LLM
from ..store import GraphStore, VectorStore
from ..types import Episode
from ..util import DAY
from .conflict import ConflictResolver
from .decay import is_durable
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
        self.conflict = ConflictResolver(
            sem_embedder, config.conflict_sim_threshold,
            llm=llm if config.conflict_llm_escalation else None)
        self.profiles = ProfileBuilder()

    def consolidate(self, episodes: list[Episode]) -> dict[str, int]:
        stats = {"facts_added": 0, "duplicates": 0, "invalidated": 0}
        chrono = sorted(episodes, key=lambda e: (e.event_time, e.ingested_at))

        # Step 1: extract facts from all episodes in parallel — each extraction is an independent LLM
        # call (no shared state). Cap concurrency at 4: with multiple bench workers each calling this,
        # an 8-wide pool meant ~24 simultaneous relay calls → throttling/backoff that was SLOWER overall.
        # 4 keeps the relay healthy while still collapsing 8 serial waits into ~2 rounds.
        # Default cap is 4 (keeps the relay healthy under many concurrent bench workers); raise via
        # ENGRAM_EXTRACT_WORKERS for bulk one-off jobs like seeding a console with a whole dataset, where
        # this is the only caller and the relay can take the extra fan-out.
        import os
        _xw = int(os.environ.get("ENGRAM_EXTRACT_WORKERS", "4"))
        ep_facts: dict[str, list] = {}
        if len(chrono) > 1:
            with ThreadPoolExecutor(max_workers=min(_xw, len(chrono))) as pool:
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
                    self.fact_store.upsert(old.id, old.embedding or [], old)
                    stats["invalidated"] += 1
                if action == "duplicate":
                    stats["duplicates"] += 1
                    continue
                self.fact_store.upsert(fact.id, fact.embedding, fact)
                self.graph_builder.add_fact(fact)
                stats["facts_added"] += 1
            ep.consolidated = True

        # Step 3: salience forgetting sweep (Bet E). Unreinforced INCIDENTAL facts fade with age, so the
        # hot set stays small at scale and stale one-offs rank below durable knowledge. Durable
        # (preference/identity) and already-reinforced facts are exempt; a floor keeps faded facts
        # retrievable (live, just deprioritized) so multi-evidence recall isn't destroyed.
        self._decay_sweep()
        return stats

    def _decay_sweep(self) -> None:
        live = [f for f in self.fact_store.values() if f.is_live()]
        if not live:
            return
        now_t = max(f.valid_at for f in live)  # latest world-time we know about = "now" for this store
        for f in live:
            if is_durable(f.predicate) or f.access_count > 0:
                continue  # who-they-are / what-they-like, and anything already used, don't fade
            days = max(0.0, (now_t - f.valid_at) / DAY)
            f.salience = max(0.3, math.exp(-self.config.salience_decay_per_day * days))

    def self_name(self, user_id: str) -> str:
        return self.extractor.self_of(user_id)

    def profile(self, user_id: str) -> dict[str, str]:
        subject = self.self_name(user_id)
        live = [f for f in self.fact_store.values() if f.user_id == user_id and f.is_live()]
        return self.profiles.build(subject, live)
