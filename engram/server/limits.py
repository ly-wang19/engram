"""Per-tenant rate limiting and Idempotency-Key replay, both dependency-free.

Two different failure modes on the same multi-tenant surface. Without a rate limit, one caller can spend
the whole process — and once the LLM-backed paths are wired, the whole budget. Without idempotency, a
client that retries after a network timeout stores the same episode twice and pays for consolidating it
twice, because the first request did succeed; only the response was lost.

**Both are in-process.** Behind several replicas each process keeps its own window and its own cache, so
the effective rate limit is `per_min x replicas` and a retry routed to a different replica will re-run.
That is an honest first step, not a distributed limiter; the shape of `RateLimiter.check` and
`IdempotencyCache.get/put` is what a Redis-backed version would replace. Saying so is better than
shipping something that looks distributed and is not.
"""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

__all__ = ["RateLimiter", "IdempotencyCache", "rate_limit_per_min", "idempotency_ttl"]

DEFAULT_IDEMPOTENCY_TTL = 86_400.0  # a day: long enough to cover a client's retry budget
DEFAULT_IDEMPOTENCY_ENTRIES = 10_000


def rate_limit_per_min() -> int:
    """Requests per tenant per minute. 0 (the default) disables limiting entirely, so the zero-setup
    demo and existing deployments behave exactly as before."""
    raw = os.environ.get("ENGRAM_RATE_LIMIT_PER_MIN", "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return max(0, value)


def idempotency_ttl() -> float:
    raw = os.environ.get("ENGRAM_IDEMPOTENCY_TTL_S", "").strip()
    if not raw:
        return DEFAULT_IDEMPOTENCY_TTL
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_IDEMPOTENCY_TTL


class RateLimiter:
    """Sliding-window limiter: at most `per_min` requests per tenant in any trailing 60 seconds."""

    def __init__(self, per_min: int, window_seconds: float = 60.0) -> None:
        self.per_min = per_min
        self.window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    @property
    def enabled(self) -> bool:
        return self.per_min > 0

    def check(self, user: str, now: Optional[float] = None) -> tuple[bool, float]:
        """Record a request and decide whether it is allowed.

        Returns `(allowed, retry_after_seconds)`. A rejected request is deliberately NOT recorded --
        otherwise a client that keeps retrying would hold its own window permanently full and never
        recover. `now` is injectable so the tests do not sleep.
        """
        if not self.enabled:
            return True, 0.0
        t = time.time() if now is None else now
        cutoff = t - self.window
        with self._lock:
            kept = [hit for hit in self._hits.get(user, ()) if hit > cutoff]
            if len(kept) >= self.per_min:
                self._hits[user] = kept
                return False, max(0.0, self.window - (t - kept[0]))
            kept.append(t)
            self._hits[user] = kept
            return True, 0.0

    def prune(self, now: Optional[float] = None) -> int:
        """Drop tenants with no hits left in the window.

        Without this the map grows by one entry per tenant that ever called and never shrinks -- a slow
        leak that only shows up on the deployment with the most tenants, which is the one that can least
        afford it. Called opportunistically rather than on a timer, so there is no background thread.
        """
        t = time.time() if now is None else now
        cutoff = t - self.window
        with self._lock:
            stale = [user for user, hits in self._hits.items() if not any(h > cutoff for h in hits)]
            for user in stale:
                del self._hits[user]
            return len(stale)

    @property
    def tracked_tenants(self) -> int:
        with self._lock:
            return len(self._hits)


class IdempotencyCache:
    """Replay the first response for an (tenant, Idempotency-Key) pair instead of re-running the work."""

    def __init__(
        self, ttl_seconds: float = DEFAULT_IDEMPOTENCY_TTL, max_entries: int = DEFAULT_IDEMPOTENCY_ENTRIES
    ) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._lock = threading.Lock()
        # Ordered so eviction can drop the oldest entry; keyed by tenant AND key so two namespaces
        # choosing the same key can never read each other's response.
        self._store: "OrderedDict[tuple[str, str], tuple[float, Any]]" = OrderedDict()

    def get(self, user: str, key: str, now: Optional[float] = None) -> Optional[Any]:
        if not key:
            return None
        t = time.time() if now is None else now
        with self._lock:
            hit = self._store.get((user, key))
            if hit is None:
                return None
            stored_at, response = hit
            if t - stored_at > self.ttl:
                self._store.pop((user, key), None)
                return None
            return response

    def put(self, user: str, key: str, response: Any, now: Optional[float] = None) -> None:
        """Cache a response. Only ever called with a successful one -- replaying a failure would turn a
        transient error into a permanent one for the lifetime of the entry."""
        if not key:
            return
        t = time.time() if now is None else now
        with self._lock:
            self._store[(user, key)] = (t, response)
            self._store.move_to_end((user, key))
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
