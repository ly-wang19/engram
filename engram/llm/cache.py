"""A disk-backed, content-addressed cache in front of any LLM — so an A/B measures the code, not the model.

Why this exists: on 2026-08-29 a read-path change was measured on LOCOMO with a same-item A/B, the
standard tool here. The comparison said single-hop lost 5 items. It had not: checking context sizes
across the two runs showed that of 83 single-hop items, ZERO produced the same token count, and the 68
that were correct in both runs differed by 481 tokens on average (extremes -2597 / +3194) — on a code
path that was byte-identical between the runs. The extractor is an LLM, so each run distilled a
different fact set from the same sessions, and every "regression" and "gain" in that experiment sat
inside that variance.

The A/B design assumes the context is fixed unless the code changes it. Pinning the LLM's replies makes
that assumption true: same prompt -> same reply, forever, so a delta between runs can only come from the
code under test. Extraction, summarization and persona building all route through `LLM.complete`, so
wrapping that one method pins all of them.

    from engram.llm.cache import CachedLLM
    llm = CachedLLM(make_llm("volcano:doubao-seed-1-6-flash-250615"), "data/llm_cache")

The first run populates the cache (and costs what it always did); later runs replay it. The answerer and
judge are deliberately NOT wrapped by the benchmark rig — pinning the thing under measurement is fine,
pinning the grader would hide real answer variance.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Optional

from .base import LLM


class CachedLLM(LLM):
    """Wrap an LLM so identical (prompt, kwargs) always return the identical reply.

    Entries are content-addressed files under `path`, so the cache survives across processes and runs,
    and two shards of one benchmark can share it. Misses fall through to the wrapped model and are
    written back; a corrupt or unreadable entry is treated as a miss rather than an error, because a
    poisoned cache must never be able to fail a benchmark run.
    """

    def __init__(self, inner: LLM, path: str, model_tag: str = "") -> None:
        self.inner = inner
        self.path = path
        # Distinguishes entries made by different models: the same prompt to doubao and to gpt-5.6 are
        # different facts, and silently serving one for the other would corrupt exactly the comparison
        # this class exists to protect.
        self.model_tag = model_tag or getattr(inner, "model", inner.__class__.__name__)
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        os.makedirs(path, exist_ok=True)

    def _key(self, prompt: str, kwargs: dict) -> str:
        payload = json.dumps(
            {"m": str(self.model_tag), "p": prompt, "k": {k: str(v) for k, v in sorted(kwargs.items())}},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _read(self, key: str) -> Optional[str]:
        try:
            with open(os.path.join(self.path, key), encoding="utf-8") as fh:
                return fh.read()
        except (OSError, UnicodeDecodeError):
            return None  # missing or unreadable -> treat as a miss, never fail the run

    def _write(self, key: str, value: str) -> None:
        target = os.path.join(self.path, key)
        tmp = f"{target}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(value)
            os.replace(tmp, target)  # atomic: a reader never sees a half-written entry
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def complete(self, prompt: str, **kwargs) -> str:
        key = self._key(prompt, kwargs)
        cached = self._read(key)
        if cached is not None:
            with self._lock:
                self.hits += 1
            return cached
        value = self.inner.complete(prompt, **kwargs)
        with self._lock:
            self.misses += 1
        if value:  # never cache an empty reply: that is the transient-failure shape the retry loop handles
            self._write(key, value)
        return value

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0}

    def __getattr__(self, name):
        # Anything the rest of the codebase reads off a model (e.g. `.model`) falls through to the real
        # one, so a cached LLM stays a drop-in replacement.
        return getattr(self.inner, name)
