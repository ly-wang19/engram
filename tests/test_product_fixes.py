"""Product read-path fixes from the field bug report (zero LongMemEval-harness impact — these paths are
search()/answer()/conflict-on-add_fact, never touched by the benchmark's lean_context):

  #1  pinned (source="user") facts must never be fuzzy-superseded across slots (the whack-a-mole bug).
  #2/#3  search() aligns the answer to the question's demanded TYPE (id/date/number/email/...), or abstains
         honestly instead of returning a type-mismatched top fact ("project ID?" -> the owner's name).
  #3b  when atomized facts can't answer, fall back to a relevant session SUMMARY (info the extractor never
       distilled — a how-to, a rule, an install command).
"""
from __future__ import annotations

from engram import Memory
from engram.consolidate.conflict import ConflictResolver
from engram.memory import _ANSWER_TYPE_MATCH, _expected_answer_type
from engram.types import Fact

_SENTINEL_EMBEDDER = object()  # non-None enables the semantic path; no .embed() call is made


def _fact(subject, predicate, obj, t, emb=None, source="extracted"):
    return Fact(subject=subject, predicate=predicate, object=obj, user_id="u", source=source,
                valid_at=float(t), created_at=float(t), embedding=emb)


# ---------------- #1 pin guard ----------------
def test_pinned_fact_never_fuzzy_supersedes_across_slots():
    # A manual (source="user") fact embeds near an unrelated single-valued fact about the same subject
    # (subject-dominated similarity). Without the guard the semantic path retires the unrelated fact.
    r = ConflictResolver(_SENTINEL_EMBEDDER, sim_threshold=0.80)
    old = _fact("王", "works_at", "字节跳动", 1, emb=[1.0, 0.0])                      # extracted, single-valued
    new = _fact("王", "knowledge_base", "某知识库", 2, emb=[1.0, 0.0], source="user")  # pinned, different slot, cos=1
    action, inv = r.reconcile(new, [old])
    assert action == "add"
    assert inv == [] and old.invalid_at is None, "a pinned fact must not whack the unrelated works_at fact"


def test_pinned_fact_still_updates_its_own_slot():
    # the guard is narrow: a pinned fact MUST still update the SAME slot (exact-slot path, not the fuzzy one).
    r = ConflictResolver(_SENTINEL_EMBEDDER, sim_threshold=0.80)
    old = _fact("王", "works_at", "字节跳动", 1, emb=[1.0, 0.0], source="user")
    new = _fact("王", "works_at", "月之暗面", 2, emb=[1.0, 0.0], source="user")
    action, inv = r.reconcile(new, [old])
    assert inv == [old] and new.supersedes == old.id, "same-slot pinned update must still supersede"


def test_extracted_cross_slot_supersession_unchanged():
    # the guard is source-specific: extracted same-attribute updates still fire (no behavior change there).
    r = ConflictResolver(_SENTINEL_EMBEDDER, sim_threshold=0.80)
    old = _fact("user", "current_city", "Beijing", 1, emb=[1.0, 0.0])
    new = _fact("user", "lives_in", "Shanghai", 2, emb=[1.0, 0.0])  # extracted, same attribute, free-form pred
    _, inv = r.reconcile(new, [old])
    assert inv == [old], "extracted same-attribute update should still supersede (unchanged)"


# ---------------- #2/#3 answer-type alignment ----------------
def test_type_helpers():
    assert _expected_answer_type("What is the project ID?") == "id"
    assert _expected_answer_type("When did I start?") is None           # bare 'when' is deliberately not a cue
    assert _expected_answer_type("Did I consider it?") is None          # 'id' must not match inside 'did'
    assert _expected_answer_type("我的工单号是多少?") == "id"
    assert _ANSWER_TYPE_MATCH["id"]("PROJ-1024") and not _ANSWER_TYPE_MATCH["id"]("Alice Wang")
    assert _ANSWER_TYPE_MATCH["email"]("a@b.com") and not _ANSWER_TYPE_MATCH["email"]("Alice")


def test_search_prefers_type_matching_fact():
    mem = Memory()
    mem.add_fact("project alpha", "owner", "Alice Wang", user_id="u1")
    mem.add_fact("project alpha", "id", "PROJ-1024", user_id="u1")
    ans = mem.search("What is the project alpha ID?", user_id="u1").answer()
    assert "PROJ-1024" in ans, f"ID question must return the ID, got: {ans!r}"


def test_search_abstains_instead_of_fabricating_wrong_type():
    mem = Memory()
    mem.add_fact("project alpha", "owner", "Alice Wang", user_id="u1")  # only a name; NO id stored
    res = mem.search("What is the project alpha ID?", user_id="u1")
    assert res.abstained, "must abstain when the demanded type isn't in memory"
    assert "Alice" not in res.answer(), "must NOT pass the owner name off as the ID"


# ---------------- #3b summary fallback ----------------
def test_summary_fallback_answers_from_summary():
    mem = Memory()
    ep = mem.add("To reset the PAT: open settings, regenerate the token, then update CI.",
                 user_id="u1", session_id="s1")
    mem.summarize_episodes([ep])
    res = mem._summary_fallback("How do I reset the PAT?", "u1")
    assert res is not None, "a relevant summary should be surfaced when no fact answers"
    assert "pat" in res.answer().lower() or "token" in res.answer().lower()


def test_search_uses_summary_fallback_when_no_fact_ranked():
    mem = Memory()
    ep = mem.add("To reset the PAT: open settings, regenerate the token, then update CI.",
                 user_id="u1", session_id="s1")
    mem.summarize_episodes([ep])

    res = mem.search("How do I reset the PAT?", user_id="u1")
    assert res.via == "summary"
    assert "token" in res.answer().lower()
    assert "(session: s1)" in res.answer()


def test_summary_fallback_can_be_disabled_for_ablation():
    from engram.config import Config

    mem = Memory(config=Config(summary_fallback=False))
    ep = mem.add("To reset the PAT: open settings, regenerate the token, then update CI.",
                 user_id="u1", session_id="s1")
    mem.summarize_episodes([ep])

    res = mem.search("How do I reset the PAT?", user_id="u1")

    assert res.abstained
    assert res.via == "abstain"


def test_summary_fallback_selects_best_source_backed_summary():
    mem = Memory()
    weak = mem.add("PAT policy note: keep credentials private.",
                   user_id="u1", session_id="weak")
    strong = mem.add("To reset the PAT: open settings, regenerate the token, then update CI.",
                     user_id="u1", session_id="strong")
    mem.summarize_episodes([weak, strong])

    res = mem._summary_fallback("How do I reset the PAT?", "u1")

    assert res is not None
    assert "(session: strong)" in res.answer()
    assert "regenerate the token" in res.answer().lower()


def test_summary_fallback_respects_as_of():
    mem = Memory()
    old = mem.add("To reset the PAT: use the legacy token page.",
                  user_id="u1", session_id="old", event_time=1_700_000_000.0)
    new = mem.add("To reset the PAT: use the new security console.",
                  user_id="u1", session_id="new", event_time=1_702_592_000.0)
    mem.summarize_episodes([old, new])

    res = mem._summary_fallback("How do I reset the PAT?", "u1", as_of=1_700_864_000.0)
    assert res is not None
    ans = res.answer().lower()
    assert "legacy token page" in ans
    assert "new security console" not in ans


# ---------------- #7 profile authority ----------------
def test_profile_build_prefers_authoritative_and_recent():
    from engram.consolidate.summarizer import ProfileBuilder
    pb = ProfileBuilder()
    extracted = _fact("Wei", "works_at", "a tech company", 1)               # vague, extracted
    pinned = _fact("Wei", "works_at", "Moonshot AI", 2, source="user")      # precise, pinned, more recent
    assert pb.build("Wei", [extracted, pinned])["works_at"] == "Moonshot AI"
    assert pb.build("Wei", [pinned, extracted])["works_at"] == "Moonshot AI", "must be order-independent"
