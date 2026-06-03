"""Cross-encoder reranker (CLAUDE.md L1 strong retrieval). A bi-encoder (BGE) gives a cheap candidate
pool; a cross-encoder rescores (query, passage) jointly for far better precision on which sessions/chunks
actually answer the question. This is the highest-leverage retrieval upgrade for LongMemEval _S."""
from __future__ import annotations

from typing import Optional


class CrossEncoderReranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-base", device: Optional[str] = None,
                 max_length: int = 512) -> None:
        from sentence_transformers import CrossEncoder  # heavy, lazy

        self.model = CrossEncoder(model_name, max_length=max_length, device=device)

    def rerank(self, query: str, candidates: list[tuple[str, str]], top_k: int) -> list[tuple[str, float]]:
        """candidates: [(id, text), ...] -> [(id, score), ...] best-first, truncated to top_k."""
        if not candidates:
            return []
        scores = self.model.predict([(query, text) for _, text in candidates], show_progress_bar=False)
        order = sorted(range(len(candidates)), key=lambda i: float(scores[i]), reverse=True)
        return [(candidates[i][0], float(scores[i])) for i in order[:top_k]]
