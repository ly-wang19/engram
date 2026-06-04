"""Salience-weighted forgetting (the MemoryBank idea, done less crudely; CLAUDE.md Bet E).

Unreinforced memories lose salience over time (multiplicative decay); accessing a memory boosts it
(spaced-repetition-style reinforcement). Salience is one term in the retrieval score and, later, the
signal a tiered store uses to evict cold memories so the hot set stays small and fast at scale.
"""
from __future__ import annotations

import math
from typing import Optional

from ..types import Fact
from ..util import DAY, now

# Durable facts exempt from forgetting (Mem0: "exempt preferences from decay"; OMEGA exempts
# preferences/error-patterns). Who the user IS and what they LIKE doesn't get stale just because it
# wasn't mentioned recently — only incidental facts fade. Kept here (not imported from retrieve/) to
# avoid a consolidate->retrieve dependency.
_DURABLE_PREDS = frozenset({
    "likes", "dislikes", "prefers", "avoids", "loves", "hates", "enjoys", "wants", "allergic_to",
    "interested_in", "name", "works_at", "lives_in", "born_in", "married_to", "occupation", "speaks",
})


def is_durable(predicate: str) -> bool:
    p = predicate.lower()
    return p in _DURABLE_PREDS or p.startswith("favorite")


def reinforce(fact: Fact, boost: float, t: Optional[float] = None) -> None:
    """Spaced-repetition reinforcement: accessing a memory boosts its salience and resets its decay clock
    (MemoryBank's spacing effect). Called when a fact is retrieved into an answer context."""
    t = now() if t is None else t
    fact.access_count += 1
    fact.last_access = t
    fact.salience += boost


def decay(fact: Fact, per_day: float, t: Optional[float] = None, exempt_durable: bool = True) -> None:
    """Multiplicative forgetting of unreinforced facts. Durable identity/preference facts are exempt by
    default — they stay salient regardless of recency, so a preference stated once long ago still surfaces."""
    if exempt_durable and is_durable(fact.predicate):
        return
    t = now() if t is None else t
    days = max(0.0, (t - fact.last_access) / DAY)
    fact.salience *= math.exp(-per_day * days)
