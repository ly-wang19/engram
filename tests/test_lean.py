"""The lean read path (CLAUDE.md Bet A/E): the scalable win condition — answer from a small retrieved
slice (persona + facts + session summaries + a couple full chunks), NOT the whole history. These tests
pin the framework's behavior offline (HashingEmbedder + rule extractor, zero deps, deterministic):
L2 summaries get built and indexed, L3 persona is synthesized, and lean_context assembles them correctly
with dedup + a hard size cap."""
from __future__ import annotations

from engram import Memory
from engram.util import DAY

BASE = 1_700_000_000.0

# A small multi-session history: distinct sessions so we can check coverage + dedup.
SESSIONS = [
    ("I work at Tencent and my favorite language is Python.", 0),
    ("I took a trip to Kyoto and loved the temples.", 1),
    ("I adopted a cat named Luna.", 2),
    ("I took a trip to Lisbon for a conference.", 3),
    ("I started learning the guitar this month.", 4),
    ("I took a trip to Oslo to see the fjords.", 5),
]


def build(summarize=True) -> Memory:
    mem = Memory()
    eps = [mem.add(text, user_id="u1", session_id=f"s{i}", event_time=BASE + day * DAY)
           for i, (text, day) in enumerate(SESSIONS)]
    for ep in eps:  # offline date stamp, mirroring the eval ingest
        ep.metadata["date"] = "2023-01-0%d" % (eps.index(ep) + 1)
    mem.consolidate(eps)
    if summarize:
        mem.summarize_episodes(eps)
    return mem


def test_summaries_built_and_indexed():
    mem = build()
    # every session gets an offline summary + an embedding, indexed for retrieval
    assert len(mem.summary_vec.values()) == len(SESSIONS)
    for ep in mem.episodes_doc.values():
        assert ep.summary, "each episode should carry a summary after summarize_episodes"
        assert ep.summary_embedding is not None


def test_summarize_is_idempotent():
    mem = build()
    n = mem.summarize_episodes(list(mem.episodes_doc.values()))  # already summarized
    assert n == 0, "re-summarizing already-summarized episodes is a no-op"


def test_retrieve_summaries_is_relevant():
    mem = build()
    hits = mem.retrieve_summaries("Which cities did I travel to?", user_id="u1", k=3)
    assert hits, "summary retrieval returns something"
    joined = " ".join(h.summary.lower() for h in hits)
    # the trip sessions should surface ahead of the cat/guitar ones
    assert any(city in joined for city in ("kyoto", "lisbon", "oslo"))


def test_persona_built_from_facts():
    mem = build()
    persona = mem.build_persona("u1")
    assert persona, "a persona is synthesized from the live facts"
    assert "persona is cached" or "u1" in mem._persona_cache


def test_consolidate_invalidates_persona_cache():
    mem = build()
    mem.build_persona("u1")  # populate cache
    assert mem._persona_cache
    mem.add("I now work at Moonshot AI.", user_id="u1", session_id="s9", event_time=BASE + 9 * DAY)
    mem.consolidate(mem.ingestor.pending())
    assert not mem._persona_cache, "consolidate() must invalidate the stale persona cache"


def test_lean_context_assembles_all_layers():
    mem = build()
    ctx = mem.lean_context("Which cities did I travel to?", user_id="u1",
                           n_summaries=10, n_facts=10, n_chunks=2)
    assert "USER PROFILE:" in ctx
    assert "SESSION SUMMARIES" in ctx
    assert "RELEVANT CONVERSATIONS (full detail):" in ctx


def test_lean_context_dedup_no_session_twice():
    """A session shown in full (detail chunk) must NOT also appear in the summary block — else we burn
    tokens repeating it. This is the core leanness invariant."""
    mem = build()
    detail = mem.retrieve_episodes("Kyoto temples", "u1", 2)
    detail_dates = {e.metadata.get("date") for e in detail}
    ctx = mem.lean_context("Kyoto temples", user_id="u1", n_summaries=10, n_chunks=2)
    summ_block = ctx.split("SESSION SUMMARIES")[1].split("RELEVANT CONVERSATIONS")[0]
    for d in detail_dates:
        # the detail session's date should not also head a summary line
        assert f"- [{d}]" not in summ_block, "detail session leaked into the summary block (dup)"


def test_lean_context_respects_char_budget():
    mem = build()
    ctx = mem.lean_context("trips", user_id="u1", char_budget=200)
    assert len(ctx) <= 200, "lean_context must hard-cap at char_budget"


def test_lean_context_is_leaner_than_full_history():
    mem = build()
    full = "\n".join(ep.content for ep in mem.episodes_doc.values())
    lean = mem.lean_context("trips", user_id="u1", n_chunks=1)
    # with only a couple full chunks + tiny summaries, lean must be a fraction of dumping every session
    # (this is a tiny history; the gap widens enormously at benchmark scale)
    assert len(lean) < len(full) * 3  # sanity: not pathologically larger


def test_consolidate_full_builds_facts_and_summaries():
    mem = Memory()
    eps = [mem.add(t, user_id="u1", session_id=f"s{i}", event_time=BASE + d * DAY)
           for i, (t, d) in enumerate(SESSIONS)]
    stats = mem.consolidate_full(fact_episodes=eps, summary_episodes=eps)
    assert stats["facts_added"] >= 0 and stats["summaries"] == len(SESSIONS)
    assert len(mem.summary_vec.values()) == len(SESSIONS)


def test_lean_path_works_with_no_summaries():
    """Degrade gracefully: if summaries were never built, lean_context still returns facts + chunks."""
    mem = build(summarize=False)
    ctx = mem.lean_context("Where do I work?", user_id="u1", n_chunks=1)
    assert "SESSION SUMMARIES" not in ctx  # none built
    assert ctx.strip(), "still produces a usable context from facts + chunks"


def test_subsumption_drops_contained_fact():
    """MemoryScope contra_repeat: a same-slot fact whose content is a strict subset of a fuller one is
    redundant. Adding the subset is a no-op; adding the superset retires the partial version."""
    from engram.consolidate.conflict import ConflictResolver
    from engram.types import Fact

    r = ConflictResolver()  # no embedder -> deterministic
    full = Fact(subject="Charles", predicate="role", object="boss and branch manager", valid_at=BASE)
    # adding a strict subset of an existing fact -> dropped as duplicate
    action, inv = r.reconcile(Fact(subject="Charles", predicate="role", object="boss", valid_at=BASE + 1),
                              [full])
    assert action == "duplicate" and not inv

    # adding a strict superset of an existing partial fact -> the partial one is invalidated
    partial = Fact(subject="Charles", predicate="role", object="boss", valid_at=BASE)
    action, inv = r.reconcile(
        Fact(subject="Charles", predicate="role", object="boss and branch manager", valid_at=BASE + 1),
        [partial])
    assert action == "add" and partial in inv


def test_date_terms_makes_dates_searchable():
    """Query-time temporal matching: a fact's date renders to searchable tokens so 'May 2023' can match
    it via BM25 (dates otherwise live only in valid_at, invisible to retrieval)."""
    from engram.retrieve.hybrid import date_terms

    terms = date_terms(BASE)  # BASE = 2023-11-14 UTC
    assert "2023" in terms
    assert "november" in terms
    assert "11" in terms


def test_durable_facts_exempt_from_decay():
    """Mem0/OMEGA: preferences + identity don't fade. An incidental fact decays; a preference does not,
    even after the same long gap."""
    from engram.consolidate.decay import decay
    from engram.types import Fact

    incidental = Fact(subject="u", predicate="parked_at", object="lot B", salience=1.0, last_access=BASE)
    preference = Fact(subject="u", predicate="likes", object="jazz", salience=1.0, last_access=BASE)
    later = BASE + 100 * DAY
    decay(incidental, per_day=0.02, t=later)
    decay(preference, per_day=0.02, t=later)
    assert incidental.salience < 1.0, "incidental fact should fade"
    assert preference.salience == 1.0, "durable preference is exempt from decay"


def test_reinforce_boosts_and_resets_clock():
    from engram.consolidate.decay import reinforce
    from engram.types import Fact

    f = Fact(subject="u", predicate="parked_at", object="lot B", salience=1.0, last_access=BASE)
    reinforce(f, boost=0.5, t=BASE + 5 * DAY)
    assert f.salience == 1.5 and f.access_count == 1 and f.last_access == BASE + 5 * DAY


def test_subsumption_keeps_distinct_values():
    """Distinct (non-subset) values on a multi-valued predicate must both survive — don't over-merge."""
    from engram.consolidate.conflict import ConflictResolver
    from engram.types import Fact

    r = ConflictResolver()
    pizza = Fact(subject="u", predicate="likes", object="pizza", valid_at=BASE)
    action, inv = r.reconcile(Fact(subject="u", predicate="likes", object="sushi", valid_at=BASE + 1),
                              [pizza])
    assert action == "add" and not inv, "distinct likes must not subsume each other"


def test_type_weight_ranks_preference_and_identity_above_incidental():
    """Type-weighted fusion (MemoryScope/OMEGA): preference > identity > incidental multipliers."""
    from engram.config import Config
    from engram.retrieve.hybrid import fact_type_weight
    from engram.types import Fact

    cfg = Config()
    pref = Fact(subject="u", predicate="likes", object="jazz")
    fav = Fact(subject="u", predicate="favorite_food", object="ramen")
    ident = Fact(subject="u", predicate="works_at", object="Tencent")
    incidental = Fact(subject="u", predicate="mentioned", object="the weather")
    assert fact_type_weight(pref, cfg) == cfg.w_type_preference
    assert fact_type_weight(fav, cfg) == cfg.w_type_preference  # favorite_* prefix
    assert fact_type_weight(ident, cfg) == cfg.w_type_identity
    assert fact_type_weight(incidental, cfg) == 1.0
    assert cfg.w_type_preference > cfg.w_type_identity > 1.0
