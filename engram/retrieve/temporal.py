"""Bi-temporal query helpers: as-of filtering and evolution-chain history (CLAUDE.md §3.1)."""
from __future__ import annotations

from typing import Optional, TypeVar

from ..types import Episode, Fact, Relation, is_visible, valid_time_for


__all__ = ["history", "is_visible", "live_at", "valid_time_for", "visible_at"]


_BiTemporal = TypeVar("_BiTemporal", Episode, Fact, Relation)


def visible_at(
    items: list[_BiTemporal],
    *,
    valid_time: float,
    transaction_time: float,
) -> list[_BiTemporal]:
    """Filter facts or relations at an explicit point on both temporal axes."""
    return [
        item
        for item in items
        if item.is_visible_at(valid_time=valid_time, transaction_time=transaction_time)
    ]


def live_at(facts: list[Fact], as_of: Optional[float]) -> list[Fact]:
    """Compatibility helper using one timestamp for both valid and transaction time."""
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
