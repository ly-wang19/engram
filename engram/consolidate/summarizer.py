"""Hierarchical abstraction -> L3 identity profile (CLAUDE.md §3).

For M0 this distills the user's current single-valued facts into a compact profile dict for O(1)
"who is this user" lookups. Session summaries and higher-level mental models (L4-L6) build on this in
later milestones; the API stays the same."""
from __future__ import annotations

from ..types import Fact


class ProfileBuilder:
    def build(self, subject: str, live_facts: list[Fact]) -> dict[str, str]:
        profile: dict[str, str] = {}
        for f in live_facts:
            if f.subject.lower() == subject.lower() and (
                f.predicate in {"works_at", "lives_in"} or f.predicate.startswith("favorite_")
            ):
                profile[f.predicate] = f.object
        return profile
