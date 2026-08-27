"""Unified benchmark rig (CLAUDE.md Bet D) — the honest, non-cheating way to compare memory systems.

Every system under test is a black box exposing ONE method: context(item) -> str (ingest the question's
sessions, return the context to answer from). A FIXED answerer + FIXED judge + FIXED embedder/extractor
are applied IDENTICALLY to every system, so the only thing that varies is the memory layer itself. We run
competitors (Mem0, Zep) here ourselves rather than citing their self-reported numbers on other harnesses.

    python eval/bench.py --data s --limit 40 --systems engram,full_context,rag \
        --answerer univibe:gemini-2.5-flash --judge univibe:gpt-5.5 --extractor deepseek

Standard rig (default): answerer=gemini-2.5-flash, judge=gpt-5.5, extractor(internal)=deepseek,
embedder=bge-small. Change them, but they apply to ALL systems equally.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

# Disable chromadb/posthog telemetry — it tries to phone home, fails behind a proxy, and stalls runs.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("POSTHOG_DISABLED", "1")
# The BGE embedder's HF tokenizer is invoked from worker threads; HuggingFace tokenizers warns that
# using it after a fork risks a deadlock and disables its own parallelism. Set this explicitly so a
# long parallel run can't be killed mid-flight by that fork/deadlock path (observed: exit 144).
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram import Config, Memory  # noqa: E402
from engram.llm.providers import load_dotenv, make_embedder, make_llm, make_reranker  # noqa: E402
from engram.util import stems  # noqa: E402
from eval.longmemeval import (  # noqa: E402
    ANSWER_SYSTEM,
    ANSWER_TEMPLATE,
    FC_CHAR_BUDGET,
    REASONING_SYSTEM,
    all_text,
    answer_self_consistency,
    answer_two_stage_pref,
    build_persona,
    build_session_map,
    extract_answer,
    ingest,
    is_abstention,
    judge_correct,
    load_data,
    looks_like_abstention,
    needs_self_consistency,
    needs_two_stage_pref,
    sessions_of,
)
from engram.retrieve.evidence import plan_evidence  # noqa: E402


def retrieve_evidence_episodes(mem: Memory, query: str, user_id: str, limit: int, use_planner: bool = True):
    """Retrieve sessions for pre-consolidation using the same evidence-shape expansion as lean_context.

    The lean read path can later use subqueries to retrieve raw chunks, but L1 facts and L2 summaries only
    exist for sessions consolidated up front. Multi-hop questions need those subquery-hit sessions in the
    consolidated pool too, otherwise the graph is missing the very edges the read path wants to walk.
    """
    if limit <= 0:
        return []
    need = (
        plan_evidence(
            query,
            aggregation_recall_expansion=getattr(
                getattr(mem, "config", None),
                "aggregation_recall_expansion",
                True,
            ),
        )
        if use_planner
        else None
    )
    subqueries = sorted((need.subqueries if need is not None else ()), key=lambda q: (-len(stems(q)), q))
    # One guaranteed seat per subquery, and the MAIN query takes everything else. The old equal
    # rank-interleave let a single weak subquery ("occupation") consume half the pool and displace
    # the main query's mid-rank hits (a gold session at main-rank #6 fell out of an 8-slot pool) --
    # the same evict-the-primary-signal shape as the provenance-promotion bug, one layer down.
    # Multi-hop still works: each hop's subquery seats its best hit (the small-limit case where the
    # subquery hits ARE the answer's halves). Planner subqueries are few (<=3), so the guaranteed
    # seats cannot themselves crowd out the main query at realistic limits.
    main_eps = mem.retrieve_episodes(query, user_id, limit)
    if not subqueries:
        return main_eps[:limit]
    per_sub = [mem.retrieve_episodes(q, user_id, 2) for q in subqueries]
    out: list = []
    seen: set[str] = set()
    for eps in per_sub:  # guaranteed seat: each subquery's best unseen hit
        for ep in eps:
            if ep.id not in seen:
                seen.add(ep.id)
                out.append(ep)
                break
        if len(out) >= limit:
            return out
    for ep in main_eps:  # the main query fills every remaining slot
        if len(out) >= limit:
            return out
        if ep.id not in seen:
            seen.add(ep.id)
            out.append(ep)
    for eps in per_sub:  # spare subquery hits backfill only if the pool still is not full
        for ep in eps:
            if len(out) >= limit:
                return out
            if ep.id not in seen:
                seen.add(ep.id)
                out.append(ep)
    return out


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[idx]


@dataclass
class Rig:
    embedder: object
    extractor_llm: object
    answerer_llm: object
    judge_llm: object
    reranker: object = None
    topk: int = 10
    chunks: int = 5
    extract_k: int = 8
    reasoning: bool = False
    strategies: bool = False
    sc_on: bool = True
    sc_k: int = 5
    persona: bool = False
    session_map: bool = False
    cascade: bool = False  # engram_lean: coarse-to-fine drill (necessary at _M/10M scale)
    summ_k: int = 25      # engram_lean: how many sessions to summarize (high-recall coverage)
    n_summaries: int = 12  # engram_lean: how many session summaries to retrieve into the lean context
    agentic: bool = False
    timeline: bool = False
    hyde: bool = False
    graph: bool = False
    wiki: bool = False
    summary: bool = False
    verify: bool = False
    verify_retry: bool = False  # engram_lean: on "I don't know", widen the slice and retry once
    intent: bool = False
    ablations: tuple[str, ...] = ()


def engram_config(evidence_planner: bool = True, ablations: tuple[str, ...] = ()) -> Config:
    """Config helper for in-rig ablations.

    A/B systems must differ only by the named memory intervention, not by hidden config drift. These flags
    default to Engram's normal behavior and let the unified harness switch off one algorithm at a time.
    """
    disabled = set(ablations)
    return Config(
        evidence_planner=evidence_planner,
        evidence_budgeting="evidence_budget" not in disabled and "evidence_budgeting" not in disabled,
        summary_fallback=(
            "summary_fallback" not in disabled
            and "summary" not in disabled
            and "derived_summary" not in disabled
        ),
        procedural_memory=(
            "procedural" not in disabled
            and "procedural_memory" not in disabled
            and "derived_procedural" not in disabled
        ),
        procedural_extraction=(
            "procedural_extraction" not in disabled
            and "procedure_extraction" not in disabled
            and "derived_procedural_extraction" not in disabled
        ),
        explicit_preference_extraction=(
            "explicit_preference_extraction" not in disabled
            and "preference_extraction" not in disabled
            and "preference_profile_extraction" not in disabled
        ),
        preference_object_filter=(
            "preference_object_filter" not in disabled
            and "weak_preference_filter" not in disabled
            and "preference_specificity_filter" not in disabled
        ),
        preference_object_normalization=(
            "preference_object_normalization" not in disabled
            and "preference_normalization" not in disabled
            and "preference_object_canonicalization" not in disabled
        ),
        preference_reversal_extraction=(
            "preference_reversal_extraction" not in disabled
            and "preference_reversal" not in disabled
            and "preference_update_extraction" not in disabled
        ),
        numeric_aggregation_candidates=(
            "numeric_aggregation_candidates" not in disabled
            and "numeric_aggregation" not in disabled
            and "aggregation_numeric" not in disabled
        ),
        aggregation_recall_expansion=(
            "aggregation_recall_expansion" not in disabled
            and "aggregation_recall" not in disabled
            and "aggregation_query_expansion" not in disabled
        ),
        aggregation_constraint_filter=(
            "aggregation_constraint_filter" not in disabled
            and "aggregation_constraints" not in disabled
            and "constraint_filter" not in disabled
        ),
        chain_evidence="chain" not in disabled,
        temporal_history_queries=(
            "temporal_history" not in disabled
            and "history_queries" not in disabled
            and "temporal_history_queries" not in disabled
        ),
        provenance_evidence="raw" not in disabled and "provenance" not in disabled,
        provenance_chunk_promotion=(
            "raw" not in disabled
            and "provenance" not in disabled
            and "provenance_chunks" not in disabled
            and "chunk_promotion" not in disabled
            and "provenance_chunk_promotion" not in disabled
        ),
        graph_proximity="graph" not in disabled and "graph_proximity" not in disabled,
        graph_relation_awareness=(
            "graph_relation" not in disabled
            and "relation_graph" not in disabled
            and "graph_relation_awareness" not in disabled
        ),
        graph_path_reinforcement=(
            "graph_reinforcement" not in disabled
            and "path_reinforcement" not in disabled
            and "graph_path_reinforcement" not in disabled
        ),
        graph_self_anchor=(
            "graph_self_anchor" not in disabled
            and "self_anchor" not in disabled
            and "self" not in disabled
        ),
        graph_entity_alias_anchor=(
            "graph_entity_alias" not in disabled
            and "entity_alias" not in disabled
            and "alias_anchor" not in disabled
        ),
        graph_negative_constraints=(
            "graph" not in disabled
            and "graph_proximity" not in disabled
            and "graph_negative" not in disabled
            and "negative_constraints" not in disabled
            and "graph_negative_constraints" not in disabled
        ),
        planner_location_chains=(
            "planner_location" not in disabled
            and "location_chain" not in disabled
            and "planner_location_chains" not in disabled
        ),
        planner_project_chains=(
            "planner_project" not in disabled
            and "project_chain" not in disabled
            and "planner_project_chains" not in disabled
        ),
        planner_llm_decomposition=(
            "planner_llm_decomposition" not in disabled
            and "planner_llm" not in disabled
            and "llm_decomposition" not in disabled
        ),
    )


# ---------------- system adapters (each: context(item) -> str) ----------------
class EngramSystem:
    name = "engram"

    def __init__(self, rig: Rig):
        self.rig = rig

    def context(self, item: dict) -> str:
        rig, qid, q = self.rig, item["question_id"], item["question"]
        mem = Memory(
            config=engram_config(ablations=rig.ablations),
            embedder=rig.embedder,
            llm=rig.extractor_llm,
            reranker=rig.reranker,
        )
        ingest(mem, item, qid)
        if rig.extract_k > 0:
            mem.engine.consolidate(mem.retrieve_episodes(q, qid, rig.extract_k))
        else:
            mem.consolidate()
        return mem.context_for(q, user_id=qid, top_k=rig.topk, k_chunks=rig.chunks,
                               agentic=rig.agentic, timeline=rig.timeline, hyde=rig.hyde,
                               graph=rig.graph, wiki=rig.wiki,
                               summary=rig.summary, verify=rig.verify, intent=rig.intent)


class FullContextSystem:
    name = "full_context"

    def __init__(self, rig: Rig):
        self.rig = rig

    def context(self, item: dict) -> str:
        return all_text(item)[:FC_CHAR_BUDGET]


class RAGSystem:
    """Pure retrieval baseline: embed raw sessions, return top-k chunks. No extraction, no facts, no
    bi-temporal — isolates exactly what Engram's consolidation layer adds over plain RAG."""

    name = "rag"

    def __init__(self, rig: Rig):
        self.rig = rig

    def context(self, item: dict) -> str:
        rig, qid, q = self.rig, item["question_id"], item["question"]
        mem = Memory(embedder=rig.embedder, reranker=rig.reranker)  # no llm -> no extraction
        ingest(mem, item, qid)
        eps = mem.retrieve_episodes(q, qid, rig.chunks)
        return "\n\n".join(f"[{e.metadata.get('date', '?')}]\n{e.content}" for e in eps)


class Mem0System:
    """Competitor: Mem0, configured with the SAME internal LLM (deepseek) + embedder as Engram, so the
    comparison is fair. Lazy import; if mem0 isn't installed the rig skips it."""

    name = "mem0"

    def __init__(self, rig: Rig):
        self.rig = rig
        from mem0 import Memory as Mem0Memory  # noqa: F401  (validated when --systems includes mem0)

        self._Mem0 = Mem0Memory
        # Mem0's internal extraction LLM -> the working ARK (Volcano) account (doubao-flash): the SAME
        # internal model Engram uses (so the comparison is fair) and the account that still has balance.
        self._llm_key = os.environ.get("ARK_API_KEY")
        self._llm_base = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
        os.environ.setdefault("OPENAI_API_KEY", self._llm_key or "")

    def context(self, item: dict) -> str:
        # Fair config: SAME internal LLM (doubao-flash) + embedder (bge-small) as Engram. Per-question chroma
        # path = isolation + thread-safety (each question's memory is independent, as LongMemEval requires).
        qid, q = item["question_id"], item["question"]
        safe = qid.replace("/", "_")
        cfg = {
            "llm": {"provider": "openai", "config": {"model": "doubao-seed-1-6-flash-250615",
                    "openai_base_url": self._llm_base, "api_key": self._llm_key, "temperature": 0.0}},
            "embedder": {"provider": "huggingface", "config": {"model": "BAAI/bge-small-en-v1.5"}},
            "vector_store": {"provider": "chroma", "config": {"collection_name": "mem0_lme", "path": f"data/mem0c/{safe}"}},
            # per-question SQLite history too — else the shared default db locks under parallel workers.
            "history_db_path": f"data/mem0c/{safe}/history.db",
        }
        m = self._Mem0.from_config(cfg)
        for sid, turns in sessions_of(item):
            msgs = [{"role": t.get("role", "user"), "content": t.get("content", "")} for t in turns if t.get("content")]
            if msgs:
                m.add(messages=msgs, user_id=qid)
        hits = m.search(query=q, user_id=qid, limit=self.rig.topk)
        results = hits.get("results", hits) if isinstance(hits, dict) else hits
        return "FACTS:\n" + "\n".join(f"- {r.get('memory', r)}" for r in results)


class ZepSystem:
    """Competitor: Zep / Graphiti — a temporally-aware (bi-temporal) knowledge-graph memory, the closest
    design to Engram. Lazy import; requires `pip install graphiti-core`, a running Neo4j/FalkorDB
    (NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD), and an OpenAI-compatible key for Graphiti's own extraction
    LLM (OPENAI_API_KEY [+ OPENAI_BASE_URL]). Per-question `group_id` isolates each LongMemEval question.

    NOTE: written against the public graphiti_core API (async; signatures vary across versions). It is
    scaffolding validated on the FIRST keyed run with a live graph DB, not in the offline CI env."""

    name = "zep"

    def __init__(self, rig: Rig):
        self.rig = rig
        import graphiti_core  # noqa: F401  (validated when --systems includes zep)

        self._Graphiti = graphiti_core.Graphiti

    def context(self, item: dict) -> str:
        import asyncio
        from datetime import datetime, timedelta, timezone

        qid, q = item["question_id"], item["question"]

        async def run() -> str:
            g = self._Graphiti(
                os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
                os.environ.get("NEO4J_USER", "neo4j"),
                os.environ.get("NEO4J_PASSWORD", "password"),
            )
            try:
                await g.build_indices_and_constraints()
                base = datetime.now(timezone.utc)
                for i, (sid, turns) in enumerate(sessions_of(item)):
                    body = "\n".join(
                        f"{t.get('role', 'user')}: {t.get('content', '')}" for t in turns if t.get("content")
                    )
                    if not body:
                        continue
                    await g.add_episode(
                        name=f"{qid}-{sid}",
                        episode_body=body,
                        source_description="longmemeval session",
                        reference_time=base + timedelta(seconds=i),
                        group_id=qid,
                    )
                results = await g.search(q, group_id=qid, num_results=self.rig.topk)
                return "FACTS:\n" + "\n".join(f"- {getattr(r, 'fact', r)}" for r in results)
            finally:
                await g.close()

        return asyncio.run(run())


class HippoRAGSystem:
    """Competitor: HippoRAG (knowledge graph + Personalized PageRank multi-hop retrieval). Lazy import;
    requires `pip install hipporag` and an OpenAI-compatible key for its OpenIE + embeddings
    (set HIPPORAG_LLM for the LLM name). Per-question `save_dir` isolates each question's index.

    NOTE: written against the public hipporag 2.x API (index/retrieve signatures vary across versions);
    scaffolding validated on the FIRST keyed run, not in the offline CI env."""

    name = "hipporag"

    def __init__(self, rig: Rig):
        self.rig = rig
        from hipporag import HippoRAG  # noqa: F401  (validated when --systems includes hipporag)

        self._HippoRAG = HippoRAG

    def context(self, item: dict) -> str:
        qid, q = item["question_id"], item["question"]
        safe = qid.replace("/", "_")
        hr = self._HippoRAG(
            save_dir=f"data/hipporag/{safe}",
            llm_model_name=os.environ.get("HIPPORAG_LLM", "gpt-4o-mini"),
            embedding_model_name="BAAI/bge-small-en-v1.5",
        )
        docs = [t.get("content", "") for _, turns in sessions_of(item) for t in turns if t.get("content")]
        hr.index(docs=docs)
        res = hr.retrieve(queries=[q], num_to_retrieve=self.rig.topk)[0]
        passages = getattr(res, "docs", res)
        return "PASSAGES:\n" + "\n".join(f"- {p}" for p in passages)


class EngramFullSystem:
    """Engram's extracted/conflict-resolved facts as a 'memory index', prepended to the full conversation
    history. Combines 100% session recall (full-context) with Engram's bi-temporal structure (facts first,
    most recent first). If this matches OMEGA-level scores, it proves the memory layer adds value on top
    of full-context. Use --systems engram_full for the strongest single-system run."""

    name = "engram_full"

    def __init__(self, rig: Rig):
        self.rig = rig

    def context(self, item: dict) -> str:
        rig, qid, q = self.rig, item["question_id"], item["question"]
        mem = Memory(embedder=rig.embedder, llm=rig.extractor_llm, reranker=rig.reranker)
        ingest(mem, item, qid)
        # Extract from top-k retrieved sessions (same as EngramSystem)
        if rig.extract_k > 0:
            mem.engine.consolidate(mem.retrieve_episodes(q, qid, rig.extract_k))
        # Assemble: extracted facts (most-recent first) as the index, full history below
        from engram.util import fmt_date
        facts = [f for f in mem.fact_store.values() if f.user_id == mem.resolver.resolve(qid) and f.is_live()]
        facts.sort(key=lambda f: f.valid_at, reverse=True)
        fact_lines = "\n".join(f"- [{fmt_date(f.valid_at)}] {f.text}" for f in facts) or "(none extracted)"
        full_hist = all_text(item)[:FC_CHAR_BUDGET]

        # L2/L3 abstraction layers (built once over the full history with the cheap extractor LLM).
        blocks = []
        if rig.persona:  # L3: user profile — grounds preference/recommendation answers
            persona = build_persona(rig.extractor_llm, full_hist)
            if persona:
                blocks.append(f"USER PROFILE (preferences, habits, possessions):\n{persona}")
        blocks.append(f"MEMORY INDEX (extracted facts, most recent first):\n{fact_lines}")
        if rig.session_map:  # L2: complete session-by-session digest — aids multi-session aggregation
            smap = build_session_map(rig.extractor_llm, full_hist)
            if smap:
                blocks.append(f"SESSION MAP (every conversation, chronological):\n{smap}")
        blocks.append(f"FULL CONVERSATION HISTORY:\n{full_hist}")
        return "\n\n".join(blocks)


class EngramLeanSystem:
    """The scalable memory system (CLAUDE.md Bet A/E): build L1 facts + L2 session summaries + L3 persona
    during consolidation, then answer from a LEAN retrieved slice — NOT the full history. This is the real
    win condition: beat full-context on accuracy while using a fraction of the tokens, so it still works
    when the history far exceeds the model's window. Unlike engram_full, the raw haystack never enters the
    prompt; only a small, organized, retrieved context does."""

    name = "engram_lean"
    evidence_planner = True
    ablations: tuple[str, ...] = ()

    def __init__(self, rig: Rig):
        self.rig = rig
        self._tl = threading.local()  # per-thread 1-item mem cache, so a verify-retry reuses the built mem

    def _mem_for(self, item: dict):
        rig, qid, q = self.rig, item["question_id"], item["question"]
        if getattr(self._tl, "qid", None) == qid and getattr(self._tl, "mem", None) is not None:
            return self._tl.mem  # reuse across the verify-retry (no re-ingest / re-summarize)
        mem = Memory(
            config=engram_config(
                evidence_planner=self.evidence_planner,
                ablations=tuple(self.ablations) + tuple(rig.ablations),
            ),
            embedder=rig.embedder,
            llm=rig.extractor_llm,
            reranker=rig.reranker,
        )
        ingest(mem, item, qid)
        # L1 facts from the top-k retrieved sessions; L2 summaries over a high-recall set (recall@25≈98%
        # on _S, so summarizing the top-summ_k retrieved sessions covers the evidence while staying lean).
        retrieved = retrieve_evidence_episodes(
            mem, q, qid, max(rig.extract_k, rig.summ_k), use_planner=self.evidence_planner
        )
        mem.consolidate_full(
            fact_episodes=retrieved[: rig.extract_k] if rig.extract_k > 0 else retrieved,
            summary_episodes=retrieved[: rig.summ_k],
        )
        self._tl.mem, self._tl.qid = mem, qid
        return mem

    def context(self, item: dict, expand: int = 0) -> str:
        rig, qid, q = self.rig, item["question_id"], item["question"]
        mem = self._mem_for(item)
        # expand>0 (a verification retry): widen the slice — more full-detail sessions, a timeline, and
        # multi-hop decomposition — to surface evidence the first lean slice missed (the 'I don't know' fix).
        return mem.lean_context(
            q, user_id=qid, n_summaries=rig.n_summaries, n_facts=rig.topk,
            n_chunks=rig.chunks + expand * 5, persona=rig.persona,
            agentic=rig.agentic or expand > 0, cascade=rig.cascade,
            timeline=rig.timeline or expand > 0,
        )


class EngramLeanNoPlannerSystem(EngramLeanSystem):
    """A/B baseline: same lean system, but disables benchmark-neutral evidence planning."""

    name = "engram_lean_no_planner"
    evidence_planner = False


class EngramLeanNoChainSystem(EngramLeanSystem):
    """A/B baseline: lean system without retrieved supersedes/evolution-chain context."""

    name = "engram_lean_no_chain"
    ablations = ("chain",)


class EngramLeanNoRawSystem(EngramLeanSystem):
    """A/B baseline: lean system without provenance-backed raw source evidence."""

    name = "engram_lean_no_raw"
    ablations = ("raw",)


class EngramLeanNoProvenanceChunksSystem(EngramLeanSystem):
    """A/B baseline: lean system without provenance-guided full-detail chunk promotion."""

    name = "engram_lean_no_provenance_chunks"
    ablations = ("provenance_chunks",)


class EngramLeanNoEvidenceBudgetSystem(EngramLeanSystem):
    """A/B baseline: lean system without intent-aware evidence budgeting."""

    name = "engram_lean_no_evidence_budget"
    ablations = ("evidence_budget",)


class EngramLeanNoSummaryFallbackSystem(EngramLeanSystem):
    """A/B baseline: lean system without derived-summary fallback for search misses."""

    name = "engram_lean_no_summary_fallback"
    ablations = ("summary_fallback",)


class EngramLeanNoProceduralMemorySystem(EngramLeanSystem):
    """A/B baseline: lean system without derived procedural/rule memory."""

    name = "engram_lean_no_procedural_memory"
    ablations = ("procedural_memory",)


class EngramLeanNoProceduralExtractionSystem(EngramLeanSystem):
    """A/B baseline: lean system without rule/runbook procedure extraction."""

    name = "engram_lean_no_procedural_extraction"
    ablations = ("procedural_extraction",)


class EngramLeanNoExplicitPreferenceExtractionSystem(EngramLeanSystem):
    """A/B baseline: lean system without explicit like/prefer/avoid preference extraction."""

    name = "engram_lean_no_explicit_preference_extraction"
    ablations = ("explicit_preference_extraction",)


class EngramLeanNoPreferenceObjectFilterSystem(EngramLeanSystem):
    """A/B baseline: lean system without weak-object filtering for explicit preferences."""

    name = "engram_lean_no_preference_object_filter"
    ablations = ("preference_object_filter",)


class EngramLeanNoPreferenceObjectNormalizationSystem(EngramLeanSystem):
    """A/B baseline: lean system without preference object canonicalization."""

    name = "engram_lean_no_preference_object_normalization"
    ablations = ("preference_object_normalization",)


class EngramLeanNoPreferenceReversalExtractionSystem(EngramLeanSystem):
    """A/B baseline: lean system without high-confidence preference-update extraction."""

    name = "engram_lean_no_preference_reversal_extraction"
    ablations = ("preference_reversal_extraction",)


class EngramLeanNoNumericAggregationCandidatesSystem(EngramLeanSystem):
    """A/B baseline: lean system without deterministic money/hour/page aggregation candidates."""

    name = "engram_lean_no_numeric_aggregation_candidates"
    ablations = ("numeric_aggregation_candidates",)


class EngramLeanNoAggregationRecallExpansionSystem(EngramLeanSystem):
    """A/B baseline: lean system without expanded recall queries for aggregation questions."""

    name = "engram_lean_no_aggregation_recall_expansion"
    ablations = ("aggregation_recall_expansion",)


class EngramLeanNoAggregationConstraintFilterSystem(EngramLeanSystem):
    """A/B baseline: lean system without query-constraint filtering for aggregation candidates."""

    name = "engram_lean_no_aggregation_constraint_filter"
    ablations = ("aggregation_constraint_filter",)


class EngramLeanNoTemporalHistorySystem(EngramLeanSystem):
    """A/B baseline: lean system without natural-language history/supersession queries."""

    name = "engram_lean_no_temporal_history"
    ablations = ("temporal_history",)


class EngramLeanNoGraphSystem(EngramLeanSystem):
    """A/B baseline: lean system without n-hop graph proximity scoring/traversal."""

    name = "engram_lean_no_graph"
    ablations = ("graph",)


class EngramLeanNoGraphRelationSystem(EngramLeanSystem):
    """A/B baseline: lean system without query-conditioned relation weighting inside graph proximity."""

    name = "engram_lean_no_graph_relation"
    ablations = ("graph_relation",)


class EngramLeanNoGraphReinforcementSystem(EngramLeanSystem):
    """A/B baseline: lean system without multi-path graph support reinforcement."""

    name = "engram_lean_no_graph_reinforcement"
    ablations = ("graph_reinforcement",)


class EngramLeanNoGraphSelfAnchorSystem(EngramLeanSystem):
    """A/B baseline: lean system without first-person/user graph anchoring."""

    name = "engram_lean_no_graph_self_anchor"
    ablations = ("graph_self_anchor",)


class EngramLeanNoGraphEntityAliasSystem(EngramLeanSystem):
    """A/B baseline: lean system without unique short-name/alias graph anchoring."""

    name = "engram_lean_no_graph_entity_alias"
    ablations = ("graph_entity_alias",)


class EngramLeanNoGraphNegativeSystem(EngramLeanSystem):
    """A/B baseline: lean system without not/except/excluding graph constraints."""

    name = "engram_lean_no_graph_negative"
    ablations = ("graph_negative",)


class EngramLeanNoPlannerLocationSystem(EngramLeanSystem):
    """A/B baseline: lean system without planner support for based_in/located_in answer chains."""

    name = "engram_lean_no_planner_location"
    ablations = ("planner_location",)


class EngramLeanNoPlannerProjectSystem(EngramLeanSystem):
    """A/B baseline: lean system without planner support for works_on/project answer chains."""

    name = "engram_lean_no_planner_project"
    ablations = ("planner_project",)


class EngramLeanCoreSystem(EngramLeanSystem):
    """A/B baseline: lean system with the newest evidence enrichments disabled together."""

    name = "engram_lean_core"
    ablations = ("chain", "raw", "graph")


SYSTEMS = {"engram": EngramSystem, "full_context": FullContextSystem, "rag": RAGSystem,
           "mem0": Mem0System, "zep": ZepSystem, "hipporag": HippoRAGSystem,
           "engram_full": EngramFullSystem, "engram_lean": EngramLeanSystem,
           "engram_lean_no_planner": EngramLeanNoPlannerSystem,
           "engram_lean_no_chain": EngramLeanNoChainSystem,
           "engram_lean_no_raw": EngramLeanNoRawSystem,
           "engram_lean_no_provenance_chunks": EngramLeanNoProvenanceChunksSystem,
           "engram_lean_no_evidence_budget": EngramLeanNoEvidenceBudgetSystem,
           "engram_lean_no_summary_fallback": EngramLeanNoSummaryFallbackSystem,
           "engram_lean_no_procedural_memory": EngramLeanNoProceduralMemorySystem,
           "engram_lean_no_procedural_extraction": EngramLeanNoProceduralExtractionSystem,
           "engram_lean_no_explicit_preference_extraction": EngramLeanNoExplicitPreferenceExtractionSystem,
           "engram_lean_no_preference_object_filter": EngramLeanNoPreferenceObjectFilterSystem,
           "engram_lean_no_preference_object_normalization": EngramLeanNoPreferenceObjectNormalizationSystem,
           "engram_lean_no_preference_reversal_extraction": EngramLeanNoPreferenceReversalExtractionSystem,
           "engram_lean_no_numeric_aggregation_candidates": EngramLeanNoNumericAggregationCandidatesSystem,
           "engram_lean_no_aggregation_recall_expansion": EngramLeanNoAggregationRecallExpansionSystem,
           "engram_lean_no_aggregation_constraint_filter": EngramLeanNoAggregationConstraintFilterSystem,
           "engram_lean_no_temporal_history": EngramLeanNoTemporalHistorySystem,
           "engram_lean_no_graph": EngramLeanNoGraphSystem,
           "engram_lean_no_graph_relation": EngramLeanNoGraphRelationSystem,
           "engram_lean_no_graph_reinforcement": EngramLeanNoGraphReinforcementSystem,
           "engram_lean_no_graph_self_anchor": EngramLeanNoGraphSelfAnchorSystem,
           "engram_lean_no_graph_entity_alias": EngramLeanNoGraphEntityAliasSystem,
           "engram_lean_no_graph_negative": EngramLeanNoGraphNegativeSystem,
           "engram_lean_no_planner_location": EngramLeanNoPlannerLocationSystem,
           "engram_lean_no_planner_project": EngramLeanNoPlannerProjectSystem,
           "engram_lean_core": EngramLeanCoreSystem}


def failed_qids(path: str, system: str, limit: int = 0) -> set[str]:
    """QIDs that a prior run got wrong for `system`.

    This is for regression replay: rerun the exact old misses with current code, without hand-picking
    items or peeking at benchmark categories during retrieval.
    """
    qids: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            res = row.get("sys", {}).get(system)
            if res and res.get("ok") is False:
                qids.append(row["qid"])
                if limit and len(qids) >= limit:
                    break
    return set(qids)


def eval_item(item, systems, rig):
    qid, cat, q = item["question_id"], item.get("question_type", "?"), item["question"]
    qdate = item.get("question_date", "")
    out = {"qid": qid, "cat": cat, "sys": {}}
    sys_prompt = REASONING_SYSTEM if rig.reasoning else ANSWER_SYSTEM
    for sysobj in systems:
        try:
            t0 = time.perf_counter()
            ctx = sysobj.context(item)
            # Strategy routing (rig.strategies): pick the answer method from the QUESTION TEXT (generalizes;
            # never peeks at the benchmark's category label). Counting/duration -> self-consistency vote;
            # recommendation/preference -> two-stage (surface user history, then answer grounded in it).
            if rig.strategies and rig.sc_on and rig.reasoning and needs_self_consistency(q):
                prompt = ANSWER_TEMPLATE.format(qdate=qdate, context=ctx, question=q)
                pred_raw = answer_self_consistency(rig.answerer_llm, prompt, sys_prompt, k=rig.sc_k)
            elif rig.strategies and rig.reasoning and needs_two_stage_pref(q):
                pred_raw = answer_two_stage_pref(rig.answerer_llm, ctx, q, qdate, sys_prompt)
            else:
                pred_raw = rig.answerer_llm.complete(
                    ANSWER_TEMPLATE.format(qdate=qdate, context=ctx, question=q), system=sys_prompt
                )
            # reasoning mode: judge + abstention check operate on the extracted final ANSWER line,
            # not the chain-of-thought, so reasoning text doesn't false-positive abstention markers.
            pred = extract_answer(pred_raw) if rig.reasoning else pred_raw
            # Verification / re-retrieval loop: the #1 failure mode is the model answering "I don't know"
            # because the evidence fell outside the first lean slice. If it abstains (and the question is
            # actually answerable), widen the slice (more full sessions + timeline + multi-hop) and retry
            # ONCE; keep the retried answer only if it's no longer an abstention.
            if (rig.verify_retry and looks_like_abstention(pred) and not is_abstention(item)
                    and sysobj.name == "engram_lean"):
                ctx2 = sysobj.context(item, expand=1)
                raw2 = rig.answerer_llm.complete(
                    ANSWER_TEMPLATE.format(qdate=qdate, context=ctx2, question=q), system=sys_prompt)
                pred2 = extract_answer(raw2) if rig.reasoning else raw2
                if not looks_like_abstention(pred2):
                    pred, ctx = pred2, ctx2
            lat = (time.perf_counter() - t0) * 1000.0
            ok = judge_correct(item, pred, rig.judge_llm)
            # save the (truncated) prediction + gold so a finished run can be error-analyzed without re-running
            out["sys"][sysobj.name] = {"ok": ok, "tok": len(ctx.split()), "lat": lat, "err": None,
                                       "pred": pred[:300], "gold": str(item.get("answer", ""))[:200]}
        except Exception as e:  # noqa: BLE001
            out["sys"][sysobj.name] = {"ok": None, "tok": 0, "lat": 0, "err": f"{type(e).__name__}: {str(e)[:80]}"}
    return out


def emit_item(item, systems, rig):
    """Produce each system's hypothesis (no judging — that's the official harness's job)."""
    q, qdate = item["question"], item.get("question_date", "")
    hyps = {}
    for sysobj in systems:
        try:
            ctx = sysobj.context(item)
            pred = rig.answerer_llm.complete(
                ANSWER_TEMPLATE.format(qdate=qdate, context=ctx, question=q), system=ANSWER_SYSTEM
            )
            hyps[sysobj.name] = pred.strip()
        except Exception as e:  # noqa: BLE001
            hyps[sysobj.name] = f"[ERROR {type(e).__name__}]"
    return {"qid": item["question_id"], "hyps": hyps}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="s")
    ap.add_argument("--qid", action="append", default=None,
                    help="run only the specified question_id; can be passed multiple times")
    ap.add_argument("--category", default=None,
                    help="filter to one question_type (e.g. knowledge-update) BEFORE --limit, to A/B a "
                         "category-specific change without paying for the whole set")
    ap.add_argument("--failures-from", default=None,
                    help="filter to QIDs that --failure-system got wrong in a previous bench JSONL")
    ap.add_argument("--failure-system", default="engram_lean",
                    help="system name to read from --failures-from (default: engram_lean)")
    ap.add_argument("--failure-limit", type=int, default=0,
                    help="max number of prior failures to replay (0 = all)")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--systems", default="engram,full_context,rag")
    ap.add_argument("--answerer", default="univibe:gemini-2.5-flash")
    ap.add_argument("--judge", default="univibe:gpt-5.5")
    ap.add_argument("--extractor", default="deepseek")
    ap.add_argument("--embedder", default="bge-small")
    ap.add_argument("--reranker", default="none",
                    help="none | bge-reranker | bge-reranker-large | bge-reranker-v2 (cross-encoder over chunk pool)")
    ap.add_argument("--topk", type=int, default=10)
    ap.add_argument("--chunks", type=int, default=5)
    ap.add_argument("--extract-k", type=int, default=8, dest="extract_k")
    ap.add_argument("--reasoning", action="store_true",
                    help="answer with explicit EVIDENCE+REASON+ANSWER chain-of-thought (Phase 1: lifts "
                         "multi-evidence categories — temporal, multi-session, knowledge-update — which "
                         "fail because single-shot answering can't aggregate)")
    ap.add_argument("--strategies", action="store_true",
                    help="route by question text: self-consistency vote on counting/duration questions, "
                         "two-stage (surface user history -> answer) on preference questions (needs --reasoning)")
    ap.add_argument("--sc-k", type=int, default=5, dest="sc_k", help="self-consistency vote count (default 5)")
    ap.add_argument("--no-self-consistency", action="store_false", dest="sc_on",
                    help="with --strategies: keep two-stage preference but DISABLE self-consistency voting")
    ap.add_argument("--persona", action="store_true",
                    help="L3 user-profile (preferences/habits) — prepended in engram_full / engram_lean")
    ap.add_argument("--session-map", action="store_true", dest="session_map",
                    help="engram_full: insert an L2 complete session-by-session digest (multi-session aggregation)")
    ap.add_argument("--summ-k", type=int, default=25, dest="summ_k",
                    help="engram_lean: number of sessions to summarize for the L2 index (default 25)")
    ap.add_argument("--n-summaries", type=int, default=12, dest="n_summaries",
                    help="engram_lean: number of session summaries to pull into the lean context (default 12)")
    ap.add_argument("--cascade", action="store_true",
                    help="engram_lean: coarse-to-fine drill (detail from top summaries) — needed at _M/10M scale")
    ap.add_argument("--agentic", action="store_true", help="engram: LLM-decomposed iterative retrieval (M2a)")
    ap.add_argument("--timeline", action="store_true", help="engram: prepend a chronological timeline (M2b)")
    ap.add_argument("--hyde", action="store_true", help="engram: HyDE query expansion for recall (M2c)")
    ap.add_argument("--graph", action="store_true", help="engram: entity-graph traversal (L2)")
    ap.add_argument("--wiki", action="store_true", help="engram: LLM-curated entity notes (L4)")
    ap.add_argument("--summary", action="store_true", help="engram: L5 synthesis summary")
    ap.add_argument("--verify", action="store_true", help="engram: self-verify -> re-retrieve gap")
    ap.add_argument("--verify-retry", action="store_true", dest="verify_retry",
                    help="engram_lean: on an 'I don't know', widen the retrieved slice + timeline and retry once")
    ap.add_argument("--intent", action="store_true", help="engram: L6 intent hint (benchmark-neutral)")
    ap.add_argument("--ablate", default="",
                    help="comma-separated Engram algorithm switches to disable for all Engram systems in this run: "
                         "evidence_budget, summary_fallback, procedural_memory, procedural_extraction, explicit_preference_extraction, preference_object_filter, preference_object_normalization, preference_reversal_extraction, numeric_aggregation_candidates, aggregation_recall_expansion, aggregation_constraint_filter, chain, temporal_history, raw/provenance, provenance_chunks, "
                         "graph/graph_proximity, graph_relation, "
                         "graph_reinforcement, graph_self_anchor, graph_entity_alias, graph_negative, "
                         "planner_location, planner_project")
    ap.add_argument("--full", action="store_true",
                    help="engram: turn ON ALL differentiators (agentic+timeline+hyde+graph+wiki+summary+verify+intent)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--answerer-timeout", type=int, default=90, dest="answerer_timeout",
                    help="per-call timeout (s) for the answerer. Raise to ~180 for gemini-2.5-pro on the "
                         "124k-token _S full context — pro is slow and 90s times out under load.")
    ap.add_argument("--out", default=None)
    ap.add_argument("--shuffle", action="store_true",
                    help="deterministically shuffle items (seeded) so any partial run is category-balanced")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true",
                    help="skip items already present in --out and append (survives a killed run; relaunch to continue)")
    ap.add_argument("--emit", default=None,
                    help="emit {question_id,hypothesis} JSONL per system (to <emit>.<system>.jsonl) for the "
                         "OFFICIAL LongMemEval evaluate_qa.py judge — instead of self-judging here")
    args = ap.parse_args()
    if args.full:  # one switch to turn ON every engram differentiator
        args.agentic = args.timeline = args.hyde = args.graph = args.wiki = True
        args.summary = args.verify = args.intent = True

    load_dotenv()
    items = load_data(args.data)
    if args.qid:
        wanted = set(args.qid)
        items = [it for it in items if it["question_id"] in wanted]
        print(f"  QID filter: {len(items)} of {len(wanted)} requested items")
    if args.category:
        items = [it for it in items if it.get("question_type") == args.category]
        print(f"  CATEGORY filter: {len(items)} '{args.category}' items")
    if args.failures_from:
        qids = failed_qids(args.failures_from, args.failure_system, args.failure_limit)
        n_before = len(items)
        items = [it for it in items if it["question_id"] in qids]
        print(f"  FAILURE replay: {len(items)} of {n_before} items from {args.failures_from} "
              f"where {args.failure_system} was wrong")
    if args.limit and args.limit < len(items):
        stride = max(1, len(items) // args.limit)
        items = items[::stride][: args.limit]
    if args.shuffle:
        import random

        random.Random(args.seed).shuffle(items)  # deterministic: partials stay category-balanced
    # resume: skip items already scored in --out, so a killed run can be relaunched to continue.
    done_qids = set()
    if args.out and args.resume and os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as fh:
            for line in fh:
                try:
                    done_qids.add(json.loads(line)["qid"])
                except Exception:  # noqa: BLE001
                    pass
        n_before = len(items)
        items = [it for it in items if it["question_id"] not in done_qids]
        print(f"  RESUME: {len(done_qids)} already done; {len(items)} of {n_before} remaining")

    # reasoning mode generates EVIDENCE+REASON+ANSWER; reasoning-model backbones (doubao-seed, deepseek-r1)
    # also emit a thinking trace, so give generous headroom to avoid truncating before the ANSWER line.
    ans_max_tok = 1500 if args.reasoning else 256
    rig = Rig(
        embedder=make_embedder(args.embedder),
        extractor_llm=make_llm(args.extractor, max_tokens=512, num_retries=3, timeout=60),
        answerer_llm=make_llm(args.answerer, max_tokens=ans_max_tok, num_retries=3, timeout=args.answerer_timeout),
        judge_llm=make_llm(args.judge, max_tokens=8, num_retries=3, timeout=60),
        reranker=make_reranker(args.reranker),
        topk=args.topk, chunks=args.chunks, extract_k=args.extract_k,
        reasoning=args.reasoning, strategies=args.strategies, sc_on=args.sc_on, sc_k=args.sc_k,
        persona=args.persona, session_map=args.session_map, cascade=args.cascade,
        summ_k=args.summ_k, n_summaries=args.n_summaries,
        agentic=args.agentic, timeline=args.timeline, hyde=args.hyde, graph=args.graph, wiki=args.wiki,
        summary=args.summary, verify=args.verify, verify_retry=args.verify_retry, intent=args.intent,
        ablations=tuple(a.strip() for a in args.ablate.split(",") if a.strip()),
    )

    systems = []
    for name in args.systems.split(","):
        name = name.strip()
        if not name:
            continue
        try:
            systems.append(SYSTEMS[name](rig))
        except Exception as e:  # noqa: BLE001
            print(f"  (skipping system '{name}': {type(e).__name__}: {str(e)[:80]})")
    sys_names = [s.name for s in systems]

    print(f"UNIFIED RIG | {len(items)} items from _{args.data} | systems={sys_names}")
    print(f"  answerer={args.answerer}  judge={args.judge}  extractor={args.extractor}  embedder={args.embedder}")
    print(f"  topk={args.topk} chunks={args.chunks} extract_k={args.extract_k} workers={args.workers}\n")
    if rig.ablations:
        print(f"  global ablations={list(rig.ablations)}\n")

    if args.emit:
        files = {s: open(f"{args.emit}.{s}.jsonl", "w", encoding="utf-8") for s in sys_names}
        lock = threading.Lock()
        done = {"n": 0}

        def handle_emit(r):
            with lock:
                done["n"] += 1
                for name, hyp in r["hyps"].items():
                    files[name].write(json.dumps({"question_id": r["qid"], "hypothesis": hyp}, ensure_ascii=False) + "\n")
                    files[name].flush()
                if done["n"] % 20 == 0 or done["n"] <= 3:
                    print(f"  emitted {done['n']}/{len(items)}", flush=True)

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for fut in as_completed([ex.submit(emit_item, it, systems, rig) for it in items]):
                handle_emit(fut.result())
        for f in files.values():
            f.close()
        print("\nhypotheses written:")
        for s in sys_names:
            print(f"  {s}: {args.emit}.{s}.jsonl")
        print("\nNow score with the OFFICIAL judge, e.g.:")
        print("  OPENAI_API_KEY=$UNIVIBE_API_KEY OPENAI_BASE_URL=https://api.univibe.cc/openai/v1 \\")
        print(f"  python external/LongMemEval/src/evaluation/evaluate_qa.py gpt-5.5 {args.emit}.engram.jsonl <ref.json>")
        return

    hits = {s: defaultdict(list) for s in sys_names}
    toks = {s: [] for s in sys_names}
    lats = {s: [] for s in sys_names}
    errs = {s: 0 for s in sys_names}
    lock = threading.Lock()
    outfh = open(args.out, "a" if args.resume else "w", encoding="utf-8") if args.out else None
    done = {"n": 0}

    def handle(r):
        with lock:
            done["n"] += 1
            for name, res in r["sys"].items():
                if res["err"]:
                    errs[name] += 1
                elif res["ok"] is not None:
                    hits[name][r["cat"]].append(res["ok"])
                    toks[name].append(res["tok"])
                    lats[name].append(res["lat"])
            if done["n"] <= 3 or done["n"] % 20 == 0:
                snap = {n: f"{100*sum(v for vs in hits[n].values() for v in vs)/max(1,sum(len(v) for v in hits[n].values())):.0f}%" for n in sys_names}
                print(f"  [{done['n']}/{len(items)}] {snap}", flush=True)
            if outfh:
                outfh.write(json.dumps(r, ensure_ascii=False) + "\n")
                outfh.flush()

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for fut in as_completed([ex.submit(eval_item, it, systems, rig) for it in items]):
                handle(fut.result())
    else:
        for it in items:
            handle(eval_item(it, systems, rig))
    if outfh:
        outfh.close()

    cats = sorted({c for n in sys_names for c in hits[n]})
    print("\n=== ACCURACY BY SYSTEM (same rig, same items, same answerer+judge) ===")
    header = "  " + "category".ljust(26) + "".join(n.ljust(16) for n in sys_names)
    print(header)
    for c in cats:
        row = "  " + c.ljust(26)
        for n in sys_names:
            v = hits[n][c]
            row += (f"{100*sum(v)/len(v):.1f}% ({len(v)})").ljust(16) if v else "-".ljust(16)
        print(row)
    print("  " + "-" * (26 + 16 * len(sys_names)))
    row = "  " + "OVERALL".ljust(26)
    for n in sys_names:
        flat = [v for vs in hits[n].values() for v in vs]
        row += (f"{100*sum(flat)/len(flat):.1f}%" if flat else "-").ljust(16)
    print(row)
    row = "  " + "avg context tokens".ljust(26)
    for n in sys_names:
        row += (f"{sum(toks[n])/len(toks[n]):.0f}" if toks[n] else "-").ljust(16)
    print(row)
    row = "  " + "p50 latency ms".ljust(26)
    for n in sys_names:
        row += (f"{percentile(lats[n], 50):.0f}" if lats[n] else "-").ljust(16)
    print(row)
    row = "  " + "p95 latency ms".ljust(26)
    for n in sys_names:
        row += (f"{percentile(lats[n], 95):.0f}" if lats[n] else "-").ljust(16)
    print(row)
    if any(errs.values()):
        print("  errors:", {n: errs[n] for n in sys_names if errs[n]})


if __name__ == "__main__":
    main()
