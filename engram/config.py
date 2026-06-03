"""Tunable knobs for retrieval fusion and consolidation. Defaults are sane for the offline demo;
on the eval harness these are tuned per benchmark (never hand-waved -- see CLAUDE.md §4)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Config:
    # --- retrieval fusion weights (CLAUDE.md §3.3) ---
    w_sem: float = 1.0  # dense semantic
    w_lex: float = 0.6  # lexical / BM25
    w_graph: float = 0.8  # n-hop graph proximity
    w_rec: float = 0.3  # recency decay
    w_sal: float = 0.25  # salience / importance

    recency_tau_days: float = 45.0
    top_k: int = 5
    candidate_k: int = 24  # per-retriever candidate pool before fusion
    rrf_k: int = 60  # Reciprocal Rank Fusion constant
    max_hops: int = 2  # multi-hop planner depth
    abstain_threshold: float = 0.45  # abstain if no attribute match AND best semantic sim below this

    # --- consolidation ---
    salience_decay_per_day: float = 0.02
    access_boost: float = 0.5
    embed_dim: int = 256
    # semantic conflict detection: invalidate a stale fact when a later same-subject fact is this
    # embedding-near (same attribute, different free-form predicate). Tuned on the harness.
    conflict_sim_threshold: float = 0.80
