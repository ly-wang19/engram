"""BM25 lexical scoring with light stemming, so "work" matches "works_at" without a learned model."""
from __future__ import annotations

import math

from ..util import stem, stems

__all__ = ["stem", "stems", "token_overlap", "bm25_scores"]


def overlap_terms(query: str, text: str) -> set[str]:
    return set(stems(query)) & set(stems(text))


def token_overlap(query: str, text: str) -> int:
    return len(overlap_terms(query, text))


def bm25_scores(query: str, docs: list[tuple[str, str]], k1: float = 1.5, b: float = 0.75) -> dict[str, float]:
    """docs: list of (id, text). Returns {id: bm25_score}."""
    if not docs:
        return {}
    doc_tokens = {doc_id: stems(text) for doc_id, text in docs}
    n = len(docs)
    avgdl = max(1.0, sum(len(t) for t in doc_tokens.values()) / n)
    df: dict[str, int] = {}
    for toks in doc_tokens.values():
        for w in set(toks):
            df[w] = df.get(w, 0) + 1
    q_terms = set(stems(query))
    scores: dict[str, float] = {}
    for doc_id, toks in doc_tokens.items():
        if not toks:
            scores[doc_id] = 0.0
            continue
        tf: dict[str, int] = {}
        for w in toks:
            tf[w] = tf.get(w, 0) + 1
        dl = len(toks)
        s = 0.0
        for w in q_terms:
            if w not in tf:
                continue
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * (tf[w] * (k1 + 1)) / (tf[w] + k1 * (1 - b + b * dl / avgdl))
        scores[doc_id] = s
    return scores
