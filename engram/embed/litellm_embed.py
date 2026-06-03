"""API embedding backend via LiteLLM (OpenAI text-embedding-3, Voyage, Cohere, Gemini, etc.).
Requires the matching provider API key in the environment. Behind the same Embedder interface."""
from __future__ import annotations

from typing import Sequence

from .base import Embedder


class LiteLLMEmbedder(Embedder):
    def __init__(self, model: str = "text-embedding-3-small", dim: int = 1536) -> None:
        import litellm  # lazy

        self._litellm = litellm
        self.model = model
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._litellm.embedding(model=self.model, input=list(texts))
        vectors = [item["embedding"] for item in resp.data]
        if vectors:
            self.dim = len(vectors[0])
        return vectors
