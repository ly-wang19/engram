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
    rrf_k: int = 60  # Reciprocal Rank Fusion constant
    max_hops: int = 2  # multi-hop planner depth
    max_hot_facts: int = 10_000  # heat-tier cap; cold facts remain durable and can page back on hot miss
    abstain_threshold: float = 0.45  # abstain if no attribute match AND best semantic sim below this
    evidence_planner: bool = True  # benchmark-neutral query -> evidence-shape routing for lean_context
    chain_evidence: bool = True  # include bounded supersedes/evolution chains for retrieved facts
    provenance_evidence: bool = True  # include compact raw source episodes for retrieved fact provenance
    graph_proximity: bool = True  # n-hop graph proximity scoring / traversal ablation switch
    graph_relation_awareness: bool = True  # query-conditioned predicate weighting inside graph proximity
    graph_path_reinforcement: bool = True  # PPR-style boost when multiple paths support the same graph node
    graph_self_anchor: bool = True  # anchor first-person/user queries to the user's own graph node

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
