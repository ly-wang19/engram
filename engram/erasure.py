"""Privacy-preserving erasure planning and in-memory verification.

Normal knowledge updates keep history.  Erasure is deliberately different: it removes the selected
source material and every derived object that could reproduce it.  Keeping planning separate from
application makes the blast radius inspectable.  It also lets the service verify the exact same
identifiers again after the durable transaction commits.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from .util import gen_id, now


def _ids(items: Iterable[Any]) -> frozenset[str]:
    return frozenset(str(item) for item in items if item)


@dataclass(frozen=True)
class ErasurePlan:
    """Content-free description of everything an erasure transaction will remove."""

    scope: str
    requested_id: str
    fact_ids: frozenset[str] = field(default_factory=frozenset)
    episode_ids: frozenset[str] = field(default_factory=frozenset)
    working_ids: frozenset[str] = field(default_factory=frozenset)
    conflict_ids: frozenset[str] = field(default_factory=frozenset)
    identity_users: frozenset[str] = field(default_factory=frozenset)

    @property
    def exists(self) -> bool:
        return bool(self.fact_ids or self.episode_ids or self.working_ids or self.conflict_ids)

    def counts(self) -> dict[str, int]:
        return {
            "facts": len(self.fact_ids),
            "episodes": len(self.episode_ids),
            "working": len(self.working_ids),
            "conflicts": len(self.conflict_ids),
        }


@dataclass(frozen=True)
class ErasureReceipt:
    """Content-free receipt returned only after the in-memory verification pass succeeds."""

    id: str
    scope: str
    requested_id: str
    erased_at: float
    counts: dict[str, int]
    digest: str
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "requested_id": self.requested_id,
            "erased_at": self.erased_at,
            "counts": dict(self.counts),
            "digest": self.digest,
            "verified": self.verified,
        }


def _all_facts(mem: Any) -> list[Any]:
    return list(mem.fact_store.values()) + list(mem.cold_store.values())


def plan_fact_erasure(mem: Any, fact_id: str) -> ErasurePlan:
    """Plan a source-level purge for one fact.

    A source episode can yield several facts.  Once its raw text is erased, every fact derived from
    that episode is removed as well: retaining a sibling extraction would defeat the user's request
    and leave dangling provenance.  User-authored facts have no episode provenance, so their blast
    radius remains the single fact.
    """
    target = mem.fact_store.get(fact_id) or mem.cold_store.get(fact_id)
    if target is None:
        return ErasurePlan(scope="fact", requested_id=fact_id)

    episode_ids = _ids(getattr(target, "provenance", ()))
    fact_ids = {fact_id}
    identity_users = {str(target.user_id)}
    if episode_ids:
        for fact in _all_facts(mem):
            if episode_ids.intersection(getattr(fact, "provenance", ())):
                fact_ids.add(fact.id)
                identity_users.add(str(fact.user_id))

    episodes = [ep for ep in mem.episodes_doc.values() if ep.id in episode_ids]
    identity_users.update(str(ep.user_id) for ep in episodes)
    # Working-memory provenance was introduced after the first snapshot format.  Match it when
    # present; old items without that metadata are covered by explicit session erasure instead of
    # guessing.
    working_ids = {
        item.id
        for item in mem.working_mem.values()
        if str(getattr(item, "metadata", {}).get("episode_id", "")) in episode_ids
    }
    conflict_ids = {
        cid
        for cid, conflict in mem.conflicts.items()
        if conflict.older in fact_ids or conflict.newer in fact_ids
    }
    return ErasurePlan(
        scope="fact",
        requested_id=fact_id,
        fact_ids=frozenset(fact_ids),
        episode_ids=episode_ids,
        working_ids=frozenset(working_ids),
        conflict_ids=frozenset(conflict_ids),
        identity_users=frozenset(identity_users),
    )


def plan_session_erasure(mem: Any, user_id: str, session_id: str) -> ErasurePlan:
    """Plan complete removal of a session and every memory object derived from it."""
    canonical = mem.resolver.resolve(user_id)
    episodes = [
        ep
        for ep in mem.episodes_doc.values()
        if mem.resolver.resolve(ep.user_id) == canonical and ep.session_id == session_id
    ]
    episode_ids = _ids(ep.id for ep in episodes)
    fact_ids = {
        fact.id
        for fact in _all_facts(mem)
        if episode_ids.intersection(getattr(fact, "provenance", ()))
    }
    working_ids = {
        item.id
        for item in mem.working_mem.values()
        if mem.resolver.resolve(item.user_id) == canonical and item.session_id == session_id
    }
    conflict_ids = {
        cid
        for cid, conflict in mem.conflicts.items()
        if conflict.older in fact_ids or conflict.newer in fact_ids
    }
    return ErasurePlan(
        scope="session",
        requested_id=session_id,
        fact_ids=frozenset(fact_ids),
        episode_ids=episode_ids,
        working_ids=frozenset(working_ids),
        conflict_ids=frozenset(conflict_ids),
        identity_users=frozenset({canonical}) if episode_ids else frozenset(),
    )


def _clear_derived_identity(mem: Any, user_ids: frozenset[str]) -> None:
    """Drop prose/alias caches that may have been learned from erased source text.

    The facts themselves remain authoritative after this conservative cache reset.  A later
    consolidation can rebuild self-name hints from the user's remaining source material.
    """
    for user_id in user_ids:
        canonical = mem.resolver.resolve(user_id)
        for handle in mem.resolver.component(canonical):
            mem._identity.pop(handle, None)
            mem._aliases.pop(handle, None)
            mem._persona_cache.pop(handle, None)
        mem._identity.pop(canonical, None)
        mem._aliases.pop(canonical, None)
        mem._persona_cache.pop(canonical, None)

    extractor = getattr(getattr(mem, "engine", None), "extractor", None)
    if extractor is not None:
        for attr in ("self_name", "aliases"):
            mapping = getattr(extractor, attr, None)
            if isinstance(mapping, dict):
                for user_id in user_ids:
                    canonical = mem.resolver.resolve(user_id)
                    mapping.pop(canonical, None)
                    for handle in mem.resolver.component(canonical):
                        mapping.pop(handle, None)


def apply_erasure(mem: Any, plan: ErasurePlan) -> ErasureReceipt:
    """Apply ``plan`` to every in-memory layer and return a verified content-free receipt."""
    for fact_id in plan.fact_ids:
        mem.graph.delete_relations_for_fact(fact_id)
        mem.fact_store.delete(fact_id)
        mem.cold_store.delete(fact_id)
    mem.graph.prune_orphan_entities()

    for episode_id in plan.episode_ids:
        mem.episodes_doc.delete(episode_id)
        mem.episodes_vec.delete(episode_id)
        mem.summary_vec.delete(episode_id)
    for working_id in plan.working_ids:
        mem.working_mem.pop(working_id, None)
    for conflict_id in plan.conflict_ids:
        mem.conflicts.pop(conflict_id, None)

    # Remove references from surviving objects.  The content-free ids are not secrets, but leaving
    # stale evolution/provenance links makes later verification and graph reconstruction unreliable.
    for fact in _all_facts(mem):
        changed = False
        if getattr(fact, "supersedes", None) in plan.fact_ids:
            fact.supersedes = None
            changed = True
        kept_provenance = [
            ep for ep in getattr(fact, "provenance", ()) if ep not in plan.episode_ids
        ]
        if kept_provenance != list(getattr(fact, "provenance", ())):
            fact.provenance = kept_provenance
            changed = True
        if changed:
            mem._upsert_fact(fact)
    mem.working_set = [fact for fact in mem.working_set if fact.id not in plan.fact_ids]
    _clear_derived_identity(mem, plan.identity_users)
    mem._persona_cache.clear()

    verified = verify_erasure(mem, plan)
    erased_at = now()
    payload = {
        "scope": plan.scope,
        "requested_id": plan.requested_id,
        "erased_at": erased_at,
        "counts": plan.counts(),
        "fact_ids": sorted(plan.fact_ids),
        "episode_ids": sorted(plan.episode_ids),
        "working_ids": sorted(plan.working_ids),
        "conflict_ids": sorted(plan.conflict_ids),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ErasureReceipt(
        id=gen_id("erase"),
        scope=plan.scope,
        requested_id=plan.requested_id,
        erased_at=erased_at,
        counts=plan.counts(),
        digest=digest,
        verified=verified,
    )


def verify_erasure(mem: Any, plan: ErasurePlan) -> bool:
    """Scan every derived layer for identifiers covered by ``plan``."""
    facts = _all_facts(mem)
    if any(fact.id in plan.fact_ids for fact in facts):
        return False
    if any(plan.episode_ids.intersection(getattr(fact, "provenance", ())) for fact in facts):
        return False
    if any(getattr(fact, "supersedes", None) in plan.fact_ids for fact in facts):
        return False
    if any(ep.id in plan.episode_ids for ep in mem.episodes_doc.values()):
        return False
    if any(ep.id in plan.episode_ids for ep in mem.episodes_vec.values()):
        return False
    if any(ep.id in plan.episode_ids for ep in mem.summary_vec.values()):
        return False
    if any(item.id in plan.working_ids for item in mem.working_mem.values()):
        return False
    if any(cid in plan.conflict_ids for cid in mem.conflicts):
        return False
    if any(
        relation.fact_id in plan.fact_ids
        for relation in mem.graph.relations()
    ):
        return False
    return not any(fact.id in plan.fact_ids for fact in mem.working_set)
