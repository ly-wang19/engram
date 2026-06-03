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


def reinforce(fact: Fact, boost: float, t: Optional[float] = None) -> None:
    t = now() if t is None else t
    fact.access_count += 1
    fact.last_access = t
    fact.salience += boost


def decay(fact: Fact, per_day: float, t: Optional[float] = None) -> None:
    t = now() if t is None else t
    days = max(0.0, (t - fact.last_access) / DAY)
    fact.salience *= math.exp(-per_day * days)
