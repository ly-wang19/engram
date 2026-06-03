"""The public facade. Wires System-1 ingest, System-2 consolidation, and the hybrid + multi-hop read
path behind a small API: add() / consolidate() / search() / as_of() / history() / profile().

Defaults are fully offline (hashing embedder, rule extractor, in-memory stores) so `Memory()` works with
zero setup. Pass a real `embedder` / `llm` / store factories to run on benchmark backends.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import Config
from .consolidate import ConsolidationEngine, reinforce
from .embed import Embedder, HashingEmbedder
from .ingest import IdentityResolver, Ingestor
from .llm import LLM
from .retrieve import HybridRetriever, MultiHopPlanner, history
from .store import (
    GraphStore,
    InMemoryDocStore,
    InMemoryGraphStore,
    InMemoryVectorStore,
    VectorStore,
)
from .retrieve.lexical import bm25_scores, overlap_terms
from .types import Episode, Fact
from .util import fmt_date

# words too generic to confirm an attribute on their own ("favorite food" must not match
# "favorite programming language" just because both contain "favorite").
_GENERIC_ATTR_TERMS = {"favorite", "favourite", "name", "is", "are", "of", "the"}


@dataclass
class SearchResult:
    query: str
    facts: list[Fact] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    via: str = "hybrid"  # "hybrid" | "multi-hop" | "abstain"
    abstained: bool = False
    _answer: Optional[str] = None

    def answer(self) -> str:
        if self.abstained:
            return "I don't have that in memory."
        if self._answer is not None:
            return self._answer
        if not self.facts:
            return "I don't have that in memory."
        top = self.facts[0]
        return top.object or top.text

    def top(self) -> Optional[Fact]:
        return self.facts[0] if self.facts else None


class Memory:
    def __init__(
        self,
        config: Optional[Config] = None,
        embedder: Optional[Embedder] = None,
        llm: Optional[LLM] = None,
        reranker=None,
        vector_store_factory: Callable[[], VectorStore] = InMemoryVectorStore,
        graph_store_factory: Callable[[], GraphStore] = InMemoryGraphStore,
    ) -> None:
        self.config = config or Config()
        self.embedder = embedder or HashingEmbedder(self.config.embed_dim)
        self.reranker = reranker  # optional cross-encoder; sharpens chunk/session retrieval (CLAUDE.md L1)
        self.llm = llm  # used by agentic retrieval (query decomposition) when enabled

        self.episodes_doc = InMemoryDocStore()
        self.episodes_vec = vector_store_factory()
        self.fact_store = vector_store_factory()
        self.graph = graph_store_factory()
        self.resolver = IdentityResolver()

        self.ingestor = Ingestor(self.episodes_doc, self.episodes_vec, self.embedder, self.resolver)
        self.engine = ConsolidationEngine(self.fact_store, self.graph, self.embedder, self.config, llm)
        self.retriever = HybridRetriever(self.fact_store, self.graph, self.embedder, self.config)
        self.planner = MultiHopPlanner(self.graph, self.fact_store, self.config)

    # --- write path ---
    def add(
        self,
        content: str,
        user_id: str = "default",
        session_id: str = "default",
        speaker: str = "user",
        event_time: Optional[float] = None,
        consolidate: bool = False,
        embedding: Optional[list] = None,
    ) -> Episode:
        ep = self.ingestor.ingest(content, user_id, session_id, speaker, event_time, embedding=embedding)
        if consolidate:
            self.consolidate()
        return ep

    def consolidate(self) -> dict[str, int]:
        return self.engine.consolidate(self.ingestor.pending())

    def link_identity(self, a: str, b: str) -> str:
        return self.resolver.link(a, b)

    # --- read path ---
    def search(
        self,
        query: str,
        user_id: str = "default",
        as_of: Optional[float] = None,
        top_k: Optional[int] = None,
    ) -> SearchResult:
        user = self.resolver.resolve(user_id)

        # 1. multi-hop planner first (fires only for genuine >=2-hop relational questions)
        plan = self.planner.plan(query, user, as_of)
        if plan is not None:
            for f in plan.facts:
                reinforce(f, self.config.access_boost)
            return SearchResult(query=query, facts=plan.facts, via="multi-hop", _answer=plan.answer)

        # 2. hybrid retrieval
        ranked, diag = self.retriever.retrieve(query, user, as_of, top_k)
        if not ranked:
            return SearchResult(query=query, via="abstain", abstained=True)

        facts = [f for f, _ in ranked]
        scores = [s for _, s in ranked]
        if self._should_abstain(query, facts, diag):
            return SearchResult(query=query, facts=facts, scores=scores, via="abstain", abstained=True)

        reinforce(facts[0], self.config.access_boost)
        return SearchResult(query=query, facts=facts, scores=scores, via="hybrid")

    def as_of(self, query: str, when: float, user_id: str = "default", top_k: Optional[int] = None) -> SearchResult:
        """Answer 'what did we believe at time `when`?' -- bi-temporal point-in-time query."""
        return self.search(query, user_id=user_id, as_of=when, top_k=top_k)

    def retrieve_episodes(self, query: str, user_id: str = "default", k: int = 5, pool: Optional[int] = None):
        """Retrieve the top-k raw episodes (sessions) for a query: bi-encoder candidate pool → BM25
        lexical rerank (RRF) → optional cross-encoder rerank.

        BM25 layer: when pool >= total episodes (LongMemEval_S: ~54 sessions, pool up to 100), all
        episodes are in candidates and the embedding rank alone misses exact-term matches (names, places,
        dates). RRF with BM25 lifts those without replacing semantic signal. Improves preference and
        exact-entity questions where the raw text terms outperform the embedding similarity.
        """
        user = self.resolver.resolve(user_id)
        pool = pool or max(k * 5, 25)
        candidates = self.episodes_vec.search(
            self.embedder.embed(query), pool, where=lambda e: e.user_id == user
        )
        eps = [ep for _, ep in candidates]

        # BM25 + embedding RRF when we have more candidates than we'll return.
        if len(eps) > k:
            bm25 = bm25_scores(query, [(ep.id, ep.content) for ep in eps])
            if bm25:
                bm25_rank = {eid: r for r, (eid, _) in
                             enumerate(sorted(bm25.items(), key=lambda x: x[1], reverse=True))}
                K_RRF = 60  # standard RRF constant — insensitive to value in [30, 100]
                fused_order = sorted(range(len(eps)), key=lambda i: -(
                    1.0 / (K_RRF + i + 1) +  # embedding rank contribution
                    1.0 / (K_RRF + bm25_rank.get(eps[i].id, len(eps)) + 1)  # BM25 rank contribution
                ))
                eps = [eps[i] for i in fused_order]

        if self.reranker is not None and len(eps) > k:
            ranked = self.reranker.rerank(query, [(i, ep.content) for i, ep in enumerate(eps)], k)
            return [eps[i] for i, _ in ranked]
        return eps[:k]

    def context_for(
        self,
        query: str,
        user_id: str = "default",
        as_of: Optional[float] = None,
        top_k: Optional[int] = None,
        k_chunks: int = 3,
        agentic: bool = False,
        timeline: bool = False,
        hyde: bool = False,
        graph: bool = False,
        wiki: bool = False,
        summary: bool = False,
        verify: bool = False,
        intent: bool = False,
    ) -> str:
        """Assemble the hybrid read context (CLAUDE.md §3) for an LLM to answer from: live, date-stamped
        facts (conflict-resolved/current state) + the top-k raw session chunks (detail extraction drops).
        Date-stamping every line is what makes temporal + knowledge-update questions answerable.

        agentic=True swaps single-shot chunk retrieval for LLM-decomposed iterative retrieval (Bet B)."""
        user = self.resolver.resolve(user_id)

        # HyDE: expand the query with an LLM-written hypothetical answer to lift retrieval recall (M2c).
        search_query = query
        if hyde and self.llm is not None:
            hypo = self.llm.complete(
                f"Write a brief, plausible hypothetical answer (1-2 sentences) to this question, to aid "
                f"retrieval:\n{query}",
                system="You write a short plausible answer. Be concise; no preamble.",
            )
            if hypo.strip():
                search_query = f"{query}\n{hypo.strip()}"

        ranked, _ = self.retriever.retrieve(search_query, user, as_of, top_k)
        # Sort most-recent first: for knowledge-update questions the LLM should see the latest
        # fact (e.g., new job, new city) at the top — and trust it over older facts lower in the list.
        ranked_by_date = sorted(ranked, key=lambda x: x[0].valid_at, reverse=True)
        fact_lines = [f"- [{fmt_date(f.valid_at)}] {f.text}" for f, _ in ranked_by_date]
        facts_block = "\n".join(fact_lines) or "(none)"

        chunk_block = ""
        if k_chunks:
            if agentic and self.llm is not None:
                from .retrieve.agentic import AgenticRetriever

                episodes = AgenticRetriever(self, self.llm).gather_episodes(query, user, k_chunks)
            else:
                episodes = self.retrieve_episodes(search_query, user, k_chunks)
            parts = []
            for ep in episodes:
                date = ep.metadata.get("date") or fmt_date(ep.event_time)
                parts.append(f"[{date}]\n{ep.content}")
            chunk_block = "\n\n".join(parts)

        result = (
            f"FACTS (current, with dates):\n{facts_block}\n\n"
            f"RELEVANT CONVERSATIONS (with dates):\n{chunk_block}"
        ).strip()
        if timeline:
            # explicit chronological ordering of the relevant facts — helps "first/after/how long" (M2b)
            ordered = sorted((f for f, _ in ranked), key=lambda f: f.valid_at)
            tl = "\n".join(f"- [{fmt_date(f.valid_at)}] {f.text}" for f in ordered) or "(none)"
            result = f"TIMELINE (oldest to newest):\n{tl}\n\n" + result
        if graph:
            # L2: traverse the entity graph from the query's anchor entities to pull connected facts
            # across sessions (multi-hop / multi-session).
            related = self._graph_related_facts(search_query, user, as_of)
            if related:
                block = "\n".join(f"- [{fmt_date(f.valid_at)}] {f.text}" for f in related)
                result += f"\n\nRELATED FACTS (graph traversal):\n{block}"
        if wiki:
            # L4: LLM-curated per-entity notes (current vs past), synthesized at query time.
            notes = self._entity_notes(search_query, user, as_of)
            if notes:
                result = "ENTITY NOTES:\n" + "\n".join(f"- {n}" for n in notes) + "\n\n" + result
        if verify and self.llm is not None:
            # self-verify: draft an answer, find the single most useful gap, retrieve it, append evidence.
            extra = self._self_verify(query, result, user, as_of)
            if extra:
                result += f"\n\nADDITIONAL EVIDENCE (self-verify):\n{extra}"
        if summary and self.llm is not None:
            # L5: synthesize the relevant material into a short faithful summary, prepended.
            syn = self.llm.complete(
                f"Synthesize, in 2-3 faithful sentences, the facts relevant to: {query}\n\n{result}",
                system="You write a concise, strictly faithful synthesis of the given context.",
            )
            if syn.strip():
                result = f"SUMMARY:\n{syn.strip()}\n\n" + result
        if intent and self.llm is not None:
            # L6: forward-looking intent hint. Honest note: not expected to help QA benchmarks; flagged
            # for completeness and ablation.
            hint = self.llm.complete(
                f"In one short phrase, what is the user likely really trying to find out with: {query}",
                system="Reply with a short phrase only.",
            )
            if hint.strip():
                result = f"LIKELY INTENT: {hint.strip()}\n\n" + result
        return result

    def _self_verify(self, query: str, context: str, user: str, as_of: Optional[float]) -> str:
        draft = self.llm.complete(
            f"Using only this context, answer concisely. If something is missing, say what.\n\n{context}\n\nQ: {query}",
            system="Answer from context; note any missing piece.",
        )
        gap = self.llm.complete(
            f"Question: {query}\nDraft answer: {draft}\nWhat ONE short search query would best fill a gap or "
            f"verify this? Reply with the query, or 'none'.",
            system="Reply with one short search query, or exactly 'none'.",
        )
        g = gap.strip().strip(".").lower()
        if not g or g == "none":
            return ""
        more = self.retrieve_episodes(gap.strip(), user, 2)
        return "\n\n".join(f"[{ep.metadata.get('date', '?')}]\n{ep.content}" for ep in more)

    def _graph_related_facts(self, query: str, user: str, as_of: Optional[float], limit: int = 8) -> list[Fact]:
        seen: dict[str, Fact] = {}
        for eid in self.retriever.query_entity_ids(query, user):
            for direction in ("out", "in"):
                for rel in self.graph.neighbors(eid, as_of, direction):
                    f = self.fact_store.get(rel.fact_id)
                    if f is not None and f.is_live(as_of):
                        seen[f.id] = f
        return list(seen.values())[:limit]

    def _entity_notes(self, query: str, user: str, as_of: Optional[float], max_entities: int = 3) -> list[str]:
        if self.llm is None:
            return []
        notes: list[str] = []
        for eid in list(self.retriever.query_entity_ids(query, user))[:max_entities]:
            ent = self.graph.entities.get(eid)
            if ent is None:
                continue
            facts = [
                f for f in self.fact_store.values()
                if f.user_id == user and f.subject.lower() == ent.name.lower()
            ]
            if not facts:
                continue
            lines = "\n".join(
                f"[{fmt_date(f.valid_at)}] {f.text}" + ("" if f.is_live(as_of) else " (past)")
                for f in sorted(facts, key=lambda x: x.valid_at)
            )
            note = self.llm.complete(
                f"Summarize what is known about {ent.name} in 2-3 sentences. Note current vs outdated "
                f"facts.\n{lines}",
                system="You write a concise, accurate entity note that resolves current vs past facts.",
            )
            if note.strip():
                notes.append(f"{ent.name}: {note.strip()}")
        return notes

    def history(self, subject: str, predicate: str, user_id: str = "default") -> list[Fact]:
        user = self.resolver.resolve(user_id)
        return history(self.fact_store.values(), user, subject, predicate)

    def profile(self, user_id: str = "default") -> dict[str, str]:
        return self.engine.profile(self.resolver.resolve(user_id))

    # --- internals ---
    def _should_abstain(self, query: str, facts: list[Fact], diag: dict) -> bool:
        """Abstain when the query's *attribute* isn't in memory -- crucially, matching the entity name
        alone is NOT enough ("Gina's favorite food" when we only know where Gina works). We require
        lexical overlap on the predicate+object, or strong semantic similarity. Targets LongMemEval
        abstention (the false-premise category)."""

        def attribute_text(f: Fact) -> str:
            return f.predicate.replace("_", " ") + " " + f.object

        for f in facts:
            if overlap_terms(query, attribute_text(f)) - _GENERIC_ATTR_TERMS:
                return False  # a non-generic attribute term matched -> the answer is in memory
        best_sem = max(diag.get("sem", {}).values(), default=0.0)
        return best_sem < self.config.abstain_threshold
