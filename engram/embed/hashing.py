"""Deterministic, dependency-free hashing embedder (the feature-hashing / "hashing trick").

It is NOT competitive with a learned embedding model -- it only captures lexical overlap. Its job is to
make the whole pipeline run and the tests pass offline. Swap in a real Embedder for benchmark runs.
"""
from __future__ import annotations

import hashlib
import math

from .base import Embedder


def _h(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")


# Recorded in the store manifest. Bumped when tokenization changes which vectors a text gets: v2 added
# CJK bigrams (before that, Chinese hashed to an empty vector). A store written under the legacy id is
# upgraded on open by re-embedding only its CJK-bearing items — ASCII text embeds identically in both.
HASHING_MODEL = "hashing-cjk2"
LEGACY_HASHING_IDS = frozenset({"HashingEmbedder"})


class HashingEmbedder(Embedder):
    model_name = HASHING_MODEL

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        from ..util import stems

        vec = [0.0] * self.dim
        toks = stems(text)
        for t in toks:
            h = _h(t)
            vec[h % self.dim] += 1.0 if (h >> 12) & 1 else -1.0
        # light bigram signal so word order matters a little
        for a, b in zip(toks, toks[1:]):
            h = _h(a + "\x1f" + b)
            vec[h % self.dim] += 0.5 if (h >> 12) & 1 else -0.5
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
