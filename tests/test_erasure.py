from __future__ import annotations

from engram import Memory
from engram.erasure import apply_erasure, plan_fact_erasure, plan_session_erasure, verify_erasure
from engram.types import Conflict


def _source_fact(mem: Memory, episode, predicate: str, object_: str):
    fact = mem.add_fact("user", predicate, object_, user_id=episode.user_id)
    fact.provenance = [episode.id]
    mem._upsert_fact(fact)
    return fact


def test_fact_erasure_purges_raw_provenance_and_every_derived_layer() -> None:
    mem = Memory()
    source = mem.add(
        "My home address is 42 Private Lane and I prefer tea.",
        user_id="u1",
        session_id="private-session",
    )
    source.summary = "Home address and drink preference."
    source.summary_embedding = mem.embedder.embed(source.summary)
    mem.summary_vec.upsert(source.id, source.summary_embedding, source)
    secret = _source_fact(mem, source, "home_address", "42 Private Lane")
    sibling = _source_fact(mem, source, "prefers", "tea")
    mem.working_set = [secret, sibling]
    conflict = Conflict(
        older=secret.id,
        newer=sibling.id,
        text_older=secret.text,
        text_newer=sibling.text,
        user_id="u1",
    )
    mem.conflicts[conflict.id] = conflict
    mem._identity["u1"] = "Alice"
    mem._aliases["u1"] = {"alice"}

    plan = plan_fact_erasure(mem, secret.id)

    assert plan.exists
    assert plan.fact_ids == {secret.id, sibling.id}
    assert plan.episode_ids == {source.id}
    receipt = apply_erasure(mem, plan)

    assert receipt.verified
    assert receipt.counts == {"facts": 2, "episodes": 1, "working": 0, "conflicts": 1}
    assert len(receipt.digest) == 64
    assert verify_erasure(mem, plan)
    assert mem.fact_store.values() == []
    assert mem.episodes_doc.get(source.id) is None
    assert mem.episodes_vec.get(source.id) is None
    assert mem.summary_vec.get(source.id) is None
    assert mem.graph.relations() == []
    assert mem.graph.entities == {}
    assert mem.working_set == []
    assert mem.conflicts == {}
    assert mem._identity == {}
    assert mem._aliases == {}


def test_fact_erasure_without_provenance_removes_only_the_manual_fact() -> None:
    mem = Memory()
    first = mem.add_fact("user", "favorite_color", "blue", user_id="u1")
    second = mem.add_fact("user", "favorite_food", "noodles", user_id="u1")

    plan = plan_fact_erasure(mem, first.id)
    receipt = apply_erasure(mem, plan)

    assert receipt.verified
    assert receipt.counts["facts"] == 1
    assert mem.fact_store.get(first.id) is None
    assert mem.fact_store.get(second.id) is second


def test_session_erasure_is_scoped_and_removes_working_memory() -> None:
    mem = Memory()
    erased_source = mem.add("Private session source", user_id="u1", session_id="erase-me")
    kept_source = mem.add("Public session source", user_id="u1", session_id="keep-me")
    erased_fact = _source_fact(mem, erased_source, "private_note", "secret")
    kept_fact = _source_fact(mem, kept_source, "public_note", "safe")
    erased_working = mem.remember_working("temporary secret", user_id="u1", session_id="erase-me")
    kept_working = mem.remember_working("temporary safe", user_id="u1", session_id="keep-me")

    plan = plan_session_erasure(mem, "u1", "erase-me")
    receipt = apply_erasure(mem, plan)

    assert receipt.verified
    assert receipt.counts == {"facts": 1, "episodes": 1, "working": 1, "conflicts": 0}
    assert mem.fact_store.get(erased_fact.id) is None
    assert mem.fact_store.get(kept_fact.id) is kept_fact
    assert mem.episodes_doc.get(erased_source.id) is None
    assert mem.episodes_doc.get(kept_source.id) is kept_source
    assert erased_working.id not in mem.working_mem
    assert kept_working.id in mem.working_mem


def test_missing_fact_erasure_plan_is_a_verified_noop() -> None:
    mem = Memory()
    plan = plan_fact_erasure(mem, "missing")

    assert not plan.exists
    receipt = apply_erasure(mem, plan)

    assert receipt.verified
    assert receipt.counts == {"facts": 0, "episodes": 0, "working": 0, "conflicts": 0}
