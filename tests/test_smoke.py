"""M0 guarantees, encoded as tests. These are the behaviors that make Engram a *memory* system and not
just RAG: knowledge-update via non-destructive supersession, bi-temporal as-of queries, multi-hop graph
reasoning, abstention, and identity resolution -- all running offline with zero dependencies."""
from __future__ import annotations

from engram import Memory
from engram.embed import HashingEmbedder
from engram.util import DAY

BASE = 1_700_000_000.0

SCRIPT = [
    ("My name is Wei and I work at Tencent.", 0),
    ("I live in Shenzhen.", 1),
    ("My favorite programming language is Python.", 2),
    ("Actually I just switched jobs — I now work at Moonshot AI.", 30),
    ("My colleague Lin works at Moonshot AI too.", 31),
]


def build() -> Memory:
    mem = Memory()
    for text, day in SCRIPT:
        mem.add(text, user_id="u1", event_time=BASE + day * DAY)
    mem.consolidate()
    return mem


def test_zero_dependency_defaults():
    mem = Memory()
    assert isinstance(mem.embedder, HashingEmbedder)


def test_single_hop_qa():
    mem = build()
    assert "shenzhen" in mem.search("Which city does Wei live in?", user_id="u1").answer().lower()
    assert "python" in mem.search("What is my favorite programming language?", user_id="u1").answer().lower()


def test_knowledge_update_supersedes_current_answer():
    mem = build()
    # the current answer must reflect the job change, not the stale fact
    assert "moonshot" in mem.search("Where does Wei work?", user_id="u1").answer().lower()


def test_no_hard_delete_history_preserved():
    mem = build()
    chain = mem.history("Wei", "works_at", user_id="u1")
    objects = {f.object for f in chain}
    assert objects == {"Tencent", "Moonshot AI"}
    live = [f for f in chain if f.is_live()]
    assert len(live) == 1 and live[0].object == "Moonshot AI"
    superseded = [f for f in chain if not f.is_live()]
    assert len(superseded) == 1 and superseded[0].object == "Tencent"
    # the new fact points back at what it replaced (the evolution chain)
    assert live[0].supersedes == superseded[0].id


def test_bitemporal_as_of_query():
    mem = build()
    before = mem.as_of("Where does Wei work?", BASE + 10 * DAY, user_id="u1")
    assert "tencent" in before.answer().lower()
    after = mem.search("Where does Wei work?", user_id="u1")
    assert "moonshot" in after.answer().lower()


def test_multi_hop_reasoning():
    mem = build()
    r = mem.search("Where does Wei's colleague work?", user_id="u1")
    assert r.via == "multi-hop"
    assert "moonshot" in r.answer().lower()


def test_abstention_unknown_attribute():
    mem = build()
    # entity is known (Wei) but the attribute (favorite food) was never stated
    r = mem.search("What is Wei's favorite food?", user_id="u1")
    assert r.abstained
    # totally unrelated query
    assert mem.search("What is the capital of France?", user_id="u1").abstained


def test_identity_resolution_merges_handles():
    mem = build()
    mem.link_identity("u1", "wei@moonshot.ai")
    r = mem.search("Where does Wei work?", user_id="wei@moonshot.ai")
    assert "moonshot" in r.answer().lower()


def test_profile_reflects_current_state():
    mem = build()
    prof = mem.profile("u1")
    assert prof.get("works_at") == "Moonshot AI"
    assert prof.get("lives_in") == "Shenzhen"
