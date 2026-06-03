"""Embedder interface. Real backends (BGE-m3, OpenAI, ...) implement this; the offline hashing
embedder is the zero-dependency default that keeps the demo + tests runnable without a network."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence


class Embedder(ABC):
    dim: int

    @abstractmethod
    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]
