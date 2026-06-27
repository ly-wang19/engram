"""M0 guarantees, encoded as tests. These are the behaviors that make Engram a *memory* system and not
just RAG: knowledge-update via non-destructive supersession, bi-temporal as-of queries, multi-hop graph
reasoning, abstention, and identity resolution -- all running offline with zero dependencies."""
from __future__ import annotations

from engram.config import Config
from engram import Memory
from engram.embed import HashingEmbedder
from engram.types import Fact
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


def test_multi_sentence_episode_extracts_every_fact():
    mem = Memory()
    mem.add("Wei works at Tencent.\nWei works at Moonshot AI.", user_id="u1", event_time=BASE)
    mem.consolidate()

    chain = mem.history("Wei", "works_at", user_id="u1")
    assert {f.object for f in chain} == {"Tencent", "Moonshot AI"}
    assert "moonshot" in mem.search("Where does Wei work?", user_id="u1").answer().lower()


def test_records_import_same_session_extracts_multiple_messages():
    mem = Memory()
    stats = mem.import_data([
        {"session_id": "career", "content": "Wei works at Tencent.", "event_time": BASE},
        {"session_id": "career", "content": "Wei works at Moonshot AI.", "event_time": BASE + DAY},
    ], format="records", user_id="u1", summarize=False)

    assert stats["sessions"] == 1
    assert stats["episodes"] == 2
    assert stats["facts_added"] == 2
    assert "tencent" in mem.as_of("Where does Wei work?", BASE + 1, user_id="u1").answer().lower()
    assert "moonshot" in mem.search("Where does Wei work?", user_id="u1").answer().lower()


def test_duplicate_live_identity_slot_reads_current_head():
    mem = Memory(config=Config(w_sem=0.0, w_lex=0.0, w_graph=0.0, w_rec=0.0, w_sal=1.0))
    old = Fact(
        "Wei",
        "works_at",
        "Tencent",
        user_id="u1",
        valid_at=BASE,
        salience=100.0,
        embedding=mem.embedder.embed("Wei works at Tencent"),
    )
    new = Fact(
        "Wei",
        "works_at",
        "Moonshot AI",
        user_id="u1",
        valid_at=BASE + DAY,
        salience=1.0,
        supersedes=old.id,
        embedding=mem.embedder.embed("Wei works at Moonshot AI"),
    )
    mem.fact_store.upsert(old.id, old.embedding or [], old)
    mem.fact_store.upsert(new.id, new.embedding or [], new)

    assert "moonshot" in mem.search("Where does Wei work?", user_id="u1").answer().lower()


def test_multi_valued_identity_facts_are_not_slot_deduped():
    mem = Memory()
    mem.add_fact("Wei", "owns", "a bike", user_id="u1")
    mem.add_fact("Wei", "owns", "a camera", user_id="u1")

    ranked, _ = mem.retriever.retrieve("What does Wei own?", mem.resolver.resolve("u1"), top_k=5)
    assert {f.object for f, _ in ranked} >= {"a bike", "a camera"}


def test_project_rules_are_structured_multi_value_memory():
    mem = Memory()
    mem.add(
        "Project rule: benchmark claims require committed raw logs before public copy changes.",
        user_id="codex-e2e",
        session_id="codex:repo:thread",
    )
    mem.add(
        "Project preference: Codex should close Engram sessions when tasks end.",
        user_id="codex-e2e",
        session_id="codex:repo:thread",
    )
    mem.consolidate()

    live = [f for f in mem.fact_store.values() if f.is_live()]
    assert {f.predicate for f in live} >= {"rule", "preference"}
    assert any(f.text.startswith("project rule ") for f in live)
    assert any(f.text.startswith("project preference ") for f in live)
    assert any("raw logs" in f.object for f in live)
    assert any("close Engram sessions" in f.object for f in live)
    assert len(live) == 2


def test_short_project_rule_context_preserves_key_constraint():
    mem = Memory()
    user = "codex-e2e"
    session = "codex:repo:thread"
    mem.add(
        "Project rule: Engram benchmark claims must be backed by committed raw logs before public copy changes.",
        user_id=user,
        session_id=session,
    )
    mem.add(
        "Project preference: Codex should use Engram as cross-agent memory and call engram_close_session when a task ends.",
        user_id=user,
        session_id=session,
    )
    mem.consolidate()

    ctx = mem.lean_context(
        "What should Codex remember about benchmark claims and session closing?",
        user_id=user,
        session_id=session,
        n_chunks=4,
    )
    assert "raw logs" in ctx
    assert "engram_close_session" in ctx


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


def test_family_profession_multi_session_reasoning():
    mem = Memory()
    mem.add("My sister Maya is a pediatrician at a children's hospital.", user_id="u1",
            session_id="s1", event_time=BASE)
    mem.add("Maya just moved to Seattle for a fellowship.", user_id="u1",
            session_id="s2", event_time=BASE + DAY)
    mem.consolidate()

    facts = {(f.subject, f.predicate, f.object) for f in mem.fact_store.values()}
    assert ("u1", "sister", "Maya") in facts
    assert ("Maya", "occupation", "pediatrician") in facts
    assert ("Maya", "lives_in", "Seattle") in facts

    r = mem.search("What is the profession of the user's sister who moved to Seattle?", user_id="u1")
    assert r.via == "multi-hop"
    assert "pediatrician" in r.answer().lower()


def test_multiple_family_relations_accumulate_and_disambiguate():
    mem = Memory()
    mem.add("My sister Anna is a lawyer.", user_id="u1", session_id="s1", event_time=BASE)
    mem.add("My sister Maya is a pediatrician.", user_id="u1", session_id="s2", event_time=BASE + DAY)
    mem.add("Maya just moved to Seattle.", user_id="u1", session_id="s3", event_time=BASE + 2 * DAY)
    mem.consolidate()

    sisters = [f for f in mem.history("u1", "sister", user_id="u1") if f.is_live()]
    assert {f.object for f in sisters} == {"Anna", "Maya"}

    r = mem.search("What is the profession of the user's sister who moved to Seattle?", user_id="u1")
    assert r.via == "multi-hop"
    assert "pediatrician" in r.answer().lower()
    evidence = {f.text for f in r.facts}
    assert "u1 sister Maya" in evidence
    assert "Maya lives in Seattle" in evidence
    assert "Maya occupation pediatrician" in evidence
    assert "u1 sister Anna" not in evidence


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


def test_first_person_occupation_reaches_profile():
    mem = Memory()
    mem.add("I am a pediatrician.", user_id="u1", event_time=BASE)
    mem.consolidate()

    assert ("u1", "occupation", "pediatrician") in {
        (f.subject, f.predicate, f.object) for f in mem.fact_store.values()
    }
    assert mem.profile("u1").get("occupation") == "pediatrician"


def test_first_person_works_as_role_is_atomic():
    mem = Memory()
    mem.add("I work as a data engineer at Spotify.", user_id="u1", event_time=BASE)
    mem.consolidate()

    assert ("u1", "occupation", "data engineer") in {
        (f.subject, f.predicate, f.object) for f in mem.fact_store.values()
    }


def test_first_person_fan_of_is_preference_not_occupation():
    mem = Memory()
    mem.add("I'm a fan of jazz.", user_id="u1", event_time=BASE)
    mem.add("I'm into synthwave.", user_id="u1", event_time=BASE + DAY)
    mem.consolidate()

    facts = {(f.subject, f.predicate, f.object) for f in mem.fact_store.values()}
    assert ("u1", "likes", "jazz") in facts
    assert ("u1", "likes", "synthwave") in facts
    assert "occupation" not in {p for _, p, _ in facts}


def test_import_style_moving_and_dietary_restrictions_extract_cleanly():
    mem = Memory()
    mem.add("I'm moving to Berlin next month for a job at Acme.", user_id="u1", event_time=BASE)
    mem.add("By the way, I'm vegetarian and allergic to peanuts.", user_id="u1", event_time=BASE + DAY)
    mem.consolidate()

    facts = {(f.subject, f.predicate, f.object) for f in mem.fact_store.values()}
    assert ("u1", "lives_in", "Berlin") in facts
    assert ("u1", "works_at", "Acme") in facts
    assert ("u1", "diet", "vegetarian") in facts
    assert ("u1", "allergic_to", "peanuts") in facts

    profile = mem.structured_profile("u1")
    dietary_items = [it for items in profile["preferences"].values() for it in items]
    assert any(it["predicate"] == "diet" and it["item"] == "vegetarian" for it in dietary_items)
    assert any(it["predicate"] == "allergic_to" and it["item"] == "peanuts" for it in dietary_items)


def test_assistant_small_talk_is_not_profile_occupation():
    mem = Memory()
    mem.add("Exciting! Berlin is great.", user_id="u1", speaker="assistant", event_time=BASE)
    mem.consolidate()

    facts = {(f.subject, f.predicate, f.object) for f in mem.fact_store.values()}
    assert ("Berlin", "occupation", "great") not in facts
    assert "occupation" not in {p for _, p, _ in facts}
