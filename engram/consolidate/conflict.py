"""Cheap, LLM-free conflict detection (CLAUDE.md Bet C).

A new fact that updates a single-valued attribute of an entity supersedes the old value. Resolution is
NON-DESTRUCTIVE: we set the old fact's `invalid_at` (world) and `expired_at` (belief) and point
`new.supersedes -> old`, preserving history so as-of queries still work.

Two detectors, both LLM-free (escalate to an LLM only for genuinely ambiguous cases — not needed here):

1. EXACT-SLOT — old and new share `(user, subject, predicate)` with a different object. Deterministic;
   the offline RuleExtractor's canonical predicates (works_at, lives_in, ...) flow through here.

2. SEMANTIC (needs a real embedder) — old and new share the *subject* and are embedding-near (same
   attribute) but the LLM extractor gave them *different free-form predicates*. This is the common,
   previously-missed case: "attends_yoga twice a week" (Aug) vs "does_yoga three times a week" (Nov) never
   shared a slot, so BOTH stayed live and the contradictory pair confused the answerer on knowledge-update
   questions. Embedding similarity over the rendered fact text catches it without an LLM call.

Single-valued is the DEFAULT: a predicate is treated as one-current-value UNLESS it is in `MULTI_VALUED`
(genuinely accumulating relations — likes, owns, visited, ...). Flipping the default (was a 3-item
allow-list) is what lets knowledge-updates actually invalidate. The semantic path is gated by similarity +
the same multi-valued guard, so accumulating preferences ("likes pizza" / "likes pasta") are never merged.
"""
from __future__ import annotations

import re
from typing import Optional

from ..embed import Embedder
from ..types import Fact
from ..util import cosine

# content words for subsumption ("Contained") detection — short/function words ignored so that
# "Charles is my boss" ⊂ "Charles is my boss and a branch manager" is recognized.
_STOP = frozenset({"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "is", "are", "was",
                   "were", "my", "his", "her", "their", "with", "for", "as", "by"})


def _content_tokens(s: str) -> frozenset:
    return frozenset(w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in _STOP)

# Predicates whose values ACCUMULATE — a person legitimately has many. These never supersede; everything
# else is treated as a single-current-value attribute (state) that a newer fact replaces.
# NB (tuned on diagnostics, not vibes): goals, aspirations, projects worked on, and DISCRETE EVENTS
# (booked/ordered/accepted/inherited/made ...) are all multi-valued — they're a log, not a mutable slot.
# Leaving them single-valued made the semantic path wrongly retire e.g. `goal reduce_sugar_intake` when a
# later `goal save_money` arrived. Do NOT add bare "does"/"attends" here — that would re-break the yoga
# frequency update (predicates "does_yoga"/"attends_yoga").
MULTI_VALUED = {
    "likes", "dislikes", "prefers", "enjoys", "loves", "hates", "avoids", "wants", "interested_in",
    "owns", "has", "bought", "purchased", "visited", "traveled_to", "went_to", "been_to",
    "knows", "met", "friends_with", "read", "watched", "played", "listened_to", "tried", "ate",
    "speaks", "allergic_to", "has_pet", "has_child", "plans", "plans_to", "recommends", "mentioned",
    "uses", "needs", "considering",
    # goals / aspirations (one can hold several at once)
    "goal", "goals", "hopes", "wishes", "aspires", "working_on", "works_on", "work_on",
    # discrete events / activity log (each is its own occurrence, not a replaceable state)
    "made", "makes", "ordered", "booked", "accepted", "inherited", "received", "sent", "gave",
    "asked", "discussed", "did", "attended", "used_service", "celebrated", "experienced",
}


def is_single_valued(predicate: str) -> bool:
    """Single-valued = current-state attribute (one value at a time). Default True; only the explicitly
    accumulating predicates in MULTI_VALUED are multi-valued. `favorite_<x>` stays single-valued (one
    favorite per x)."""
    p = predicate.lower()
    if p in MULTI_VALUED:
        return False
    # "likes_<x>" / "owns_<x>" style compounds also accumulate
    return not any(p.startswith(mv + "_") for mv in MULTI_VALUED)


def _norm(s: str) -> str:
    return s.strip().lower()


def _supersede(old: Fact, new: Fact) -> None:
    """Non-destructive invalidation: old stops being valid-in-world at new.valid_at and stops being
    believed at new.created_at; new records what it replaced. History is preserved for as-of queries."""
    if old.invalid_at is None:
        old.invalid_at = new.valid_at
    old.expired_at = new.created_at
    new.supersedes = old.id


class ConflictResolver:
    def __init__(self, embedder: Optional[Embedder] = None, sim_threshold: float = 0.80) -> None:
        # embedder is used ONLY for the semantic path; when None (offline/hashing) only exact-slot fires,
        # keeping the zero-dep demo + tests fully deterministic.
        self.embedder = embedder
        self.sim_threshold = sim_threshold

    def reconcile(self, new: Fact, live: list[Fact]) -> tuple[str, list[Fact]]:
        """Return (action, invalidated_facts). action is "add" or "duplicate"."""
        same_slot = [f for f in live if f.slot == new.slot]

        # exact same claim already known -> dedup (no new fact, no invalidation)
        for old in same_slot:
            if _norm(old.object) == _norm(new.object):
                return ("duplicate", [])

        invalidated: list[Fact] = []

        # Subsumption / "Contained" (MemoryScope contra_repeat): if the new claim's content words are a
        # STRICT SUBSET of an existing same-slot fact, the new fact adds nothing -> drop it (dedup). If it
        # strictly SUPERSETS an existing one, the old is a less-complete version of the same claim ->
        # invalidate it. This prunes redundant near-duplicates that otherwise crowd the retrieved context
        # (Bet A: precision). Gated to same-slot so accumulating values on the same predicate stay distinct.
        new_toks = _content_tokens(new.object)
        if new_toks:
            for old in same_slot:
                old_toks = _content_tokens(old.object)
                if not old_toks or old_toks == new_toks:
                    continue
                if new_toks < old_toks:  # new ⊂ old: subsumed, nothing gained
                    return ("duplicate", [])
                if old_toks < new_toks and new.valid_at >= old.valid_at:  # new ⊃ old: more complete
                    _supersede(old, new)
                    invalidated.append(old)

        if is_single_valued(new.predicate):
            _seen = {id(f) for f in invalidated}  # don't double-invalidate what subsumption already took
            # 1. exact-slot: same (subject, predicate), different object
            for old in same_slot:
                if id(old) in _seen:
                    continue
                if _norm(old.object) != _norm(new.object) and new.valid_at >= old.valid_at:
                    _supersede(old, new)
                    invalidated.append(old)

            # 2. semantic: same subject, embedding-near (same attribute under a different free-form
            #    predicate), different object, and the new fact is same-or-later in valid time.
            if self.embedder is not None and new.embedding:
                done = {id(f) for f in invalidated}
                for old in live:
                    if id(old) in done or old.slot == new.slot:
                        continue  # already handled by exact-slot, or it's the identical slot
                    if _norm(old.subject) != _norm(new.subject) or _norm(old.object) == _norm(new.object):
                        continue
                    if not is_single_valued(old.predicate) or not old.embedding:
                        continue
                    if new.valid_at < old.valid_at:
                        continue
                    if cosine(new.embedding, old.embedding) >= self.sim_threshold:
                        _supersede(old, new)
                        invalidated.append(old)

        # point supersedes at the most-recent fact we replaced (the head of the evolution chain)
        if invalidated:
            new.supersedes = max(invalidated, key=lambda f: f.valid_at).id
        return ("add", invalidated)
