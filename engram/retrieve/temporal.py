"""Bi-temporal query helpers: as-of filtering and evolution-chain history (CLAUDE.md §3.1)."""
from __future__ import annotations

from typing import Optional

from ..types import Fact


def live_at(facts: list[Fact], as_of: Optional[float]) -> list[Fact]:
    return [f for f in facts if f.is_live(as_of)]


def history(facts: list[Fact], user_id: str, subject: str, predicate: str) -> list[Fact]:
    """The full evolution of a slot over time (live + superseded), oldest-first."""
    chain = [
        f
        for f in facts
        if f.user_id == user_id
        and f.subject.lower() == subject.lower()
        and f.predicate == predicate
    ]
    return sorted(chain, key=lambda f: f.valid_at)
