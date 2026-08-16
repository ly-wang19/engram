"""Tunable knobs for retrieval fusion and consolidation. Defaults are sane for the offline demo;
on the eval harness these are tuned per benchmark (never hand-waved -- see CLAUDE.md §4)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Config:
    # --- storage backend selection ---
    storage: str = "memory"  # memory | durable | lancedb
    data_path: Optional[str] = None

    # --- retrieval fusion weights (CLAUDE.md §3.3) ---
    w_sem: float = 1.0  # dense semantic
    w_lex: float = 0.6  # lexical / BM25
    w_graph: float = 0.8  # n-hop graph proximity  (do NOT cut: load-bearing for multi-session cross-session
    #                       links — tuning it down to 0.4 raised dev recall@15 +4.1 but cratered multi-session
    #                       QA -23 on the full set. recall@15 is a misleading proxy; keep graph weight high.)
    w_rec: float = 0.3  # recency decay
    w_sal: float = 0.25  # salience / importance

    # Type-weighted retrieval (MemoryScope `type_ratio`, OMEGA 2x-for-decisions): a durable fact about who
    # the user IS or what they PREFER outranks an incidental mention OF SIMILAR RELEVANCE. Critically the
    # multiplier is applied to the SEMANTIC score — which is ~0 for irrelevant facts — so it can only
    # reorder among already-relevant candidates, never pull an off-topic fact to the top (an earlier
    # version multiplied the fused rank score and wrongly surfaced 'favorite_language' for "where do you
    # work?"). Gentle by design; gated to a real embedder (the hashing fallback's cosines are noise).
    w_type_preference: float = 1.25
    w_type_identity: float = 1.15

    recency_tau_days: float = 45.0
    top_k: int = 5
    candidate_k: int = 24  # per-retriever candidate pool before fusion
    # Bounded candidate retrieval (Bet E). OFF by default: it changes which facts get *scored*, and the
    # published numbers were produced by the full scan, so turning it on silently would break the
    # "every number traces to a committed log" rule. With `candidate_pool` >= the live fact count the two
    # paths are provably identical (tests/test_bounded_candidates.py) — the flag buys speed at scale, and
    # its ranking behaviour at scale still needs a keyed harness run before it becomes the default.
    bounded_candidates: bool = False
    candidate_pool: int = 400  # per-channel candidate budget before fusion when bounded_candidates is on
    # Whether bounded retrieval asks the vector store for semantic candidates. Measured cost/benefit:
    # neither shipped backend has a real ANN index (the in-memory store brute-forces cosine and sorts;
    # LanceDB materialises the whole table whenever a Python predicate is supplied), so this channel
    # re-introduces the very O(n) pass the candidate pool exists to avoid. Leaving it ON is the
    # recall-safe default; turning it OFF is what makes the read path genuinely bounded, at the cost of
    # missing facts that are semantically relevant but share no query term. Flip it off only with a real
    # ANN backend, or after a keyed harness run shows the recall loss is acceptable.
    candidate_vector_channel: bool = True
    # Segment size for reranking long documents. Cross-encoders read a bounded window (512 tokens for the
    # BGE rerankers) and truncate silently past it, so raw sessions are scored segment-by-segment and take
    # their best segment's score. ~300 words sits inside that window once the query is added.
    rerank_segment_words: int = 300
    rrf_k: int = 60  # Reciprocal Rank Fusion constant
    max_hops: int = 2  # multi-hop planner depth
    max_hot_facts: int = 10_000  # heat-tier cap; cold facts remain durable and can page back on hot miss
    abstain_threshold: float = 0.45  # abstain if no attribute match AND best semantic sim below this
    evidence_planner: bool = True  # benchmark-neutral query -> evidence-shape routing for lean_context
    evidence_budgeting: bool = True  # intent-aware context packing instead of equal/raw truncation
    explicit_preference_extraction: bool = True  # extract I like/prefer/avoid style preference facts
    preference_object_filter: bool = True  # suppress weak explicit preference objects like "it"/"these ideas"
    preference_object_normalization: bool = True  # canonicalize "sound/idea of X" preference objects to X
    preference_reversal_extraction: bool = True  # extract high-confidence "no longer like X" preference updates
    numeric_aggregation_candidates: bool = True  # extract money/hour/page rows for count/sum questions
    aggregation_recall_expansion: bool = True  # expand count/sum queries into high-recall evidence lookups
    aggregation_constraint_filter: bool = True  # mark numeric candidates that violate query constraints as EXCLUDE
    procedural_extraction: bool = True  # extract durable runbooks/how-to steps into typed procedure facts
    summary_fallback: bool = True  # answer from derived session summaries when facts cannot answer
    summary_fallback_k: int = 6  # candidate summaries inspected by the fallback before abstaining
    procedural_memory: bool = True  # surface standing rules/how-to memories as a typed derived read layer
    procedural_memory_k: int = 6  # candidate procedural facts inspected before abstaining or context packing
    chain_evidence: bool = True  # include bounded supersedes/evolution chains for retrieved facts
    temporal_history_queries: bool = True  # answer before/previous/former queries from supersession chains
    provenance_chunk_promotion: bool = True  # promote retrieved facts' source episodes into raw chunks
    provenance_evidence: bool = True  # include compact raw source episodes for retrieved fact provenance
    graph_proximity: bool = True  # n-hop graph proximity scoring / traversal ablation switch
    graph_relation_awareness: bool = True  # query-conditioned predicate weighting inside graph proximity
    graph_path_reinforcement: bool = True  # PPR-style boost when multiple paths support the same graph node
    graph_self_anchor: bool = True  # anchor first-person/user queries to the user's own graph node
    graph_entity_alias_anchor: bool = True  # anchor unique entity-name/alias tokens (e.g. "Moonshot" -> Moonshot AI)
    graph_negative_constraints: bool = True  # honor not/except/excluding entity constraints in graph retrieval
    planner_location_chains: bool = True  # let multi-hop planner answer based_in/located_in/headquarters chains
    planner_project_chains: bool = True  # let multi-hop planner walk works_on/project -> based_in chains

    # --- consolidation ---
    salience_decay_per_day: float = 0.02
    access_boost: float = 0.5
    embed_dim: int = 256
    # semantic conflict detection: invalidate a stale fact when a later same-subject fact is this
    # embedding-near (same attribute, different free-form predicate). Tuned on the harness.
    conflict_sim_threshold: float = 0.80
    # escalate borderline conflicts (similarity just below threshold) to an LLM adjudicator (§3.2 step 3).
    # Off by default: keeps consolidation LLM-free per fact (Bet C); enable for max precision.
    conflict_llm_escalation: bool = False
    # System-2 LLM conflict DETECTION for the ambiguous tail: surface suspected conflicts the cheap rules
    # can't enumerate as pending items for the USER to confirm (never auto-resolved). Off by default so
    # offline / tests stay deterministic; enable in production for the detect->confirm loop.
    conflict_detection: bool = False
