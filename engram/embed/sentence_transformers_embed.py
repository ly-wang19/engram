"""Local embedding backend via sentence-transformers (e.g. BAAI/bge-*). No API key required -- the model
is downloaded from HuggingFace once and run locally. This is the default *real* embedder for benchmarks;
the offline HashingEmbedder remains the zero-dependency fallback."""
from __future__ import annotations

import threading
from typing import Optional, Sequence

from .base import Embedder


class SentenceTransformerEmbedder(Embedder):
    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        device: Optional[str] = None,
        normalize: bool = True,
        batch_size: int = 64,
    ) -> None:
        from sentence_transformers import SentenceTransformer  # heavy import, kept lazy

        self.model = SentenceTransformer(model_name, device=device)
        self.normalize = normalize
        self.batch_size = batch_size
        self.dim = int(self.model.get_sentence_embedding_dimension())
        # A single torch model is NOT safe to call concurrently from multiple threads (the GPU/MPS backend
        # aborts under parallel encode). The eval harness runs questions on a thread pool, so serialize
        # encode here. This is cheap: batched encode is fast and no longer the wall-clock bottleneck.
        self._lock = threading.Lock()

    def embed(self, text: str) -> list[float]:
        with self._lock:
            vec = self.model.encode([text], normalize_embeddings=self.normalize, show_progress_bar=False)[0]
        return vec.tolist()

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            arr = self.model.encode(
                list(texts),
                normalize_embeddings=self.normalize,
                batch_size=self.batch_size,
                show_progress_bar=False,
            )
        return [row.tolist() for row in arr]
