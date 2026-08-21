"""Live service metrics — the charter's own discipline (Bet D) turned on the running service.

The architecture states a <50ms target for the write path and <100ms for the read path. Without live
percentiles those are assertions, not measurements, and the same goes for the token-saving claim: it is
reported from offline benchmark logs but never from what the service actually served.

Pure stdlib, fixed memory, and **aggregate-only by construction** — operation latencies, counters, and
token totals. No namespace names, no queries, no content, so the endpoint can stay as open as /health
without leaking one tenant's existence to another.
"""
from __future__ import annotations

import functools
import threading
import time
from collections import Counter, defaultdict, deque
from typing import Optional

__all__ = ["Metrics", "timed"]


def _pct(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile over a sorted sample. No interpolation — for an SLO readout the extra
    precision would be false: the sample is a bounded window, not the full population."""
    if not sorted_vals:
        return 0.0
    index = min(int(q * (len(sorted_vals) - 1) + 0.5), len(sorted_vals) - 1)
    return sorted_vals[index]


class Metrics:
    """Thread-safe metrics with a bounded footprint.

    Latencies live in a sliding window per operation, so percentiles describe how the service is behaving
    *now* rather than averaging away a regression under months of history. Counters are monotonic.
    """

    def __init__(self, window: int = 512) -> None:
        self._lock = threading.Lock()
        self._lat: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
        self._counts: Counter = Counter()
        # Token accounting is kept in two buckets on purpose. `_ctx_total` is every served context, which
        # is the honest total volume. The savings ratio may only be computed from calls where BOTH sides
        # were measured -- dividing a total full-history figure by a total context figure would compare
        # different sets of calls and understate the saving whenever a caller skipped the baseline.
        self._ctx_total = 0
        self._paired_ctx = 0
        self._paired_full = 0
        self._paired_n = 0
        self._started = time.time()

    # --- recording ---

    def observe(self, op: str, seconds: float) -> None:
        with self._lock:
            self._lat[op].append(seconds)
            self._counts[op] += 1

    def count(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counts[name] += n

    def tokens(self, context: int, full: Optional[int] = None) -> None:
        """Record one served context's size, and the full-history baseline when the caller computed it."""
        with self._lock:
            self._ctx_total += int(context)
            if full is not None:
                self._paired_ctx += int(context)
                self._paired_full += int(full)
                self._paired_n += 1

    # --- reading ---

    def snapshot(self) -> dict:
        """The /metrics payload: numbers only, no user data by construction."""
        with self._lock:
            ops = {}
            for op, window in self._lat.items():
                if not window:
                    continue
                sample = sorted(window)
                ops[op] = {
                    "n": self._counts[op],
                    "p50_ms": round(_pct(sample, 0.50) * 1000, 2),
                    "p95_ms": round(_pct(sample, 0.95) * 1000, 2),
                    "avg_ms": round(sum(sample) / len(sample) * 1000, 2),
                    "max_ms": round(sample[-1] * 1000, 2),
                    "window": len(sample),
                }
            counts = {k: v for k, v in self._counts.items() if k not in ops}
            tokens = {
                "context_total": self._ctx_total,
                "baseline_context_total": self._paired_ctx,
                "baseline_full_total": self._paired_full,
                "calls_with_baseline": self._paired_n,
                # The live version of the headline "~8x fewer tokens": full history over served context,
                # across the calls that measured both. None until at least one such pair exists -- a
                # made-up ratio would be worse than no ratio.
                "savings_ratio": (
                    round(self._paired_full / self._paired_ctx, 2) if self._paired_ctx else None
                ),
            }
            return {
                "uptime_s": round(time.time() - self._started, 1),
                "ops": ops,
                "counts": counts,
                "tokens": tokens,
            }


def timed(op: str):
    """Record a MemoryService method's wall-clock under `op`.

    Reads `self.metrics` at call time rather than binding it at decoration, so a service constructed
    without metrics (or re-initialised) still works and the decorator costs nothing at import.
    """

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            started = time.perf_counter()
            try:
                return fn(self, *args, **kwargs)
            finally:
                meter = getattr(self, "metrics", None)
                if meter is not None:
                    meter.observe(op, time.perf_counter() - started)

        return wrapper

    return deco
