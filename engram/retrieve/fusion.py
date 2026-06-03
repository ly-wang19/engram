"""Reciprocal Rank Fusion. Scale-robust way to combine multiple ranked signals (semantic, lexical,
graph, recency, salience) without normalizing incomparable score ranges. Realizes CLAUDE.md §3.3."""
from __future__ import annotations


def weighted_rrf(rankings: dict[str, list[str]], weights: dict[str, float], k: int = 60) -> dict[str, float]:
    """rankings: {signal_name: [doc_id, ...] best-first}. weights: {signal_name: weight}."""
    fused: dict[str, float] = {}
    for name, ordered in rankings.items():
        w = weights.get(name, 1.0)
        for rank, doc_id in enumerate(ordered):
            fused[doc_id] = fused.get(doc_id, 0.0) + w * (1.0 / (k + rank + 1))
    return fused


def order_by_score(scores: dict[str, float]) -> list[str]:
    return [doc_id for doc_id, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]
