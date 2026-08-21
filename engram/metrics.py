"""Live service metrics (CLAUDE.md Bet D, applied to serving): the same triple we report offline —
latency + tokens + volume — measured on the running service. The write path has a <50ms System-1 target
and the read path a <100ms target (§3); without live percentiles those targets are vibes, not numbers.

Pure stdlib and deliberately AGGREGATE-ONLY: operation latencies, counters, and token totals — never user
content, namespace names, or query text, so the /metrics endpoint can stay as open as /health.
"""
from __future__ import annotations

import functools
import threading
import time
from collections import Counter, defaultdict, deque
from typing import Optional


def _pct(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile over a sorted sample (no interpolation; fine for SLO dashboards)."""
    return sorted_vals[min(int(q * (len(sorted_vals) - 1) + 0.5), len(sorted_vals) - 1)]


class Metrics:
    """Thread-safe, fixed-memory metrics: a sliding window of recent latencies per operation (percentiles
    reflect *current* behavior, not the all-time mix), monotonic counters, and token-saving totals."""

    def __init__(self, window: int = 512) -> None:
        self._lock = threading.Lock()
        self._lat: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
        self._counts: Counter = Counter()
        self._ctx_tokens = 0  # total tokens of assembled (lean) contexts served
        self._full_tokens = 0  # total full-history baseline tokens, when computed alongside
        self._with_baseline = 0  # how many recalls measured both (the savings denominator)
        self._started = time.time()

    # --- recording -----------------------------------------------------------
    def observe(self, op: str, seconds: float) -> None:
        with self._lock:
            self._lat[op].append(seconds)
            self._counts[op] += 1

    def count(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counts[name] += n

    def tokens(self, context: int, full: Optional[int] = None) -> None:
        """Record one served context's size; `full` (the full-history token count) only when the caller
        computed it — the savings ratio is derived from pairs where both sides were measured."""
        with self._lock:
            self._ctx_tokens += int(context)
            if full is not None:
                self._full_tokens += int(full)
                self._with_baseline += 1

    # --- reading -------------------------------------------------------------
    def snapshot(self) -> dict:
        """JSON-able aggregate view (the /metrics payload). Numbers only — no user data by construction."""
        with self._lock:
            ops = {}
            for op, window in self._lat.items():
                if not window:
                    continue
                s = sorted(window)
                ops[op] = {
                    "n": self._counts[op],
                    "p50_ms": round(_pct(s, 0.50) * 1000, 2),
                    "p95_ms": round(_pct(s, 0.95) * 1000, 2),
                    "avg_ms": round(sum(s) / len(s) * 1000, 2),
                    "max_ms": round(s[-1] * 1000, 2),
                    "window": len(s),
                }
            counts = {k: v for k, v in self._counts.items() if k not in ops}
            tokens = {
                "context_total": self._ctx_tokens,
                "full_total": self._full_tokens,
                "recalls_with_baseline": self._with_baseline,
                # the live "~8x fewer tokens" number: full-history cost / served-context cost, over the
                # recalls where both were measured. None until there's at least one such pair.
                "savings_ratio": (round(self._full_tokens / self._ctx_tokens, 2)
                                  if self._ctx_tokens and self._full_tokens else None),
            }
            return {"uptime_s": round(time.time() - self._started, 1),
                    "ops": ops, "counts": counts, "tokens": tokens}


def timed(op: str):
    """Decorator for MemoryService methods: record the call's wall-clock under `op`. Fetches
    `self.metrics` at call time, so it adds nothing to construction and survives service re-init."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(self, *args, **kwargs)
            finally:
                m = getattr(self, "metrics", None)
                if m is not None:
                    m.observe(op, time.perf_counter() - t0)
        return wrapper
    return deco
