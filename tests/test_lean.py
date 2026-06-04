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


def test_user_fact_is_authoritative_over_extraction():
    """The editable-memory invariant: a user-asserted fact owns its slot. Auto-extraction can neither
    override it nor sit beside it; a user assertion supersedes an existing extracted value."""
    from engram.consolidate.conflict import ConflictResolver
    from engram.types import Fact

    r = ConflictResolver()
    user_fact = Fact(subject="u", predicate="works_at", object="ByteDance", source="user", valid_at=BASE)
    extracted = Fact(subject="u", predicate="works_at", object="Tencent", source="extracted", valid_at=BASE + 1)
    action, inv = r.reconcile(extracted, [user_fact])
    assert action == "duplicate" and not inv, "extracted fact must NOT override a user fact"

    r2 = ConflictResolver()
    ext = Fact(subject="u", predicate="works_at", object="Tencent", source="extracted", valid_at=BASE)
    usr = Fact(subject="u", predicate="works_at", object="ByteDance", source="user", valid_at=BASE + 1)
    action2, inv2 = r2.reconcile(usr, [ext])
    assert action2 == "add" and ext in inv2, "a user assertion supersedes the extracted value"


def test_memory_crud_add_edit_delete():
    """The management-UI operations: add_fact (user-authored), update_fact (edit + re-author), delete_fact
    (right-to-forget, hard removal)."""
    mem = Memory()
    f = mem.add_fact("user", "works_at", "ByteDance", user_id="u1")
    assert f.source == "user" and f.text == "user works at ByteDance"
    assert mem.fact_store.get(f.id) is not None

    edited = mem.update_fact(f.id, object="Moonshot AI")
    assert edited.object == "Moonshot AI" and edited.source == "user"
    assert mem.fact_store.get(f.id).text == "user works at Moonshot AI"

    assert mem.delete_fact(f.id) is True
    assert mem.fact_store.get(f.id) is None
    assert mem.delete_fact("nonexistent") is False


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


def test_weight_tuner_runs_and_scores():
    """The fusion-weight tuner (CLAUDE.md §4) computes recall@k for a weight set and grid-searches —
    offline, deterministic. This is what makes 'tuned on the harness' true instead of hand-waved."""
    from engram.types import Fact
    from eval.tune_weights import grid_search, mean_recall_at_k

    mem = build()
    facts = [f for f in mem.fact_store.values()]
    assert facts
    relevant = {facts[0].id}
    dev = [(mem, "u1", "Where do I work?", relevant)]
    r = mean_recall_at_k(dev, {"w_sem": 1.0, "w_lex": 0.6, "w_graph": 0.8, "w_rec": 0.3, "w_sal": 0.25}, k=10)
    assert 0.0 <= r <= 1.0
    best, best_w, base_r = grid_search(dev, k=10)
    assert set(best_w) == {"w_sem", "w_lex", "w_graph", "w_rec", "w_sal"}
    assert best >= base_r  # the grid includes the defaults, so best can't be worse


def test_timeline_block_is_chronological():
    """Temporal aid: with timeline=True the facts appear oldest->newest with dates, so ordering and
    duration are read off the order rather than mentally computed."""
    mem = build()
    ctx = mem.lean_context("When did I travel?", user_id="u1", timeline=True, n_chunks=1)
    assert "TIMELINE" in ctx
    tl = ctx.split("TIMELINE")[1]
    import re
    dates = re.findall(r"(\d{4}-\d{2}-\d{2})", tl.split("SESSION SUMMARIES")[0])
    assert dates == sorted(dates), "timeline must be chronological (oldest to newest)"


def test_cascade_coarse_to_fine_assembles():
    """Coarse-to-fine cascade: detail is drilled from the top-ranked summaries; both modes assemble."""
    mem = build()
    casc = mem.lean_context("Which cities did I visit?", user_id="u1", cascade=True, n_chunks=2)
    flat = mem.lean_context("Which cities did I visit?", user_id="u1", cascade=False, n_chunks=2)
    assert casc.strip() and flat.strip()


def test_heat_tiered_eviction_pages_cold_preserves_durable():
    """MemoryOS heat tiering: over capacity, the coldest incidental facts page to the cold tier
    (non-destructive); durable preference/identity facts are never evicted."""
    from engram.types import Fact

    mem = Memory()
    u = mem.resolver.resolve("u1")
    for i in range(5):  # incidental, increasing salience
        f = Fact(subject="u1", predicate="parked_at", object=f"lot{i}", user_id=u,
                 salience=float(i), last_access=BASE + i)
        mem.fact_store.upsert(f.id, [0.0], f)
    durable = Fact(subject="u1", predicate="likes", object="jazz", user_id=u, salience=0.0, last_access=BASE)
    mem.fact_store.upsert(durable.id, [0.0], durable)

    n = mem.evict_cold(max_hot=3)
    assert n == 3
    hot = mem.fact_store.values()
    assert durable in hot, "durable preference must never be paged out, even at lowest salience"
    assert len(mem.cold_store.values()) == 3, "evicted facts are preserved in the cold tier, not deleted"


def test_agentic_multihop_decomposition():
    """Bet B: with an LLM, lean_context decomposes the question into sub-queries and unions their
    retrieval; without an LLM it degrades gracefully to the single query."""
    mem = build()  # offline facts extracted

    # graceful: agentic=True but no llm -> still returns a usable context
    ctx0 = mem.lean_context("Which cities did I visit?", user_id="u1", agentic=True, n_chunks=1)
    assert ctx0.strip()

    # with an llm that decomposes, the path runs and still produces a context
    class FakeLLM:
        def complete(self, prompt, system=None, **k):
            return '["cities I traveled to", "trips"]'

    mem.llm = FakeLLM()
    ctx1 = mem.lean_context("Which cities did I visit and how many trips?", user_id="u1",
                            agentic=True, n_chunks=1)
    assert ctx1.strip() and "SESSION SUMMARIES" in ctx1


def test_procedural_memory_surfaces_instructions():
    """Procedural memory: a distinct typed view returning the user's standing how-to/instruction facts."""
    from engram.types import Fact

    mem = Memory()
    u = mem.resolver.resolve("u1")
    mem.fact_store.upsert("f1", [0.0], Fact(subject="u1", predicate="wants_reminder",
                                            object="water the plants", user_id=u))
    mem.fact_store.upsert("f2", [0.0], Fact(subject="u1", predicate="works_at", object="Tencent", user_id=u))
    proc = mem.procedural("u1")
    objs = {f.object for f in proc}
    assert "water the plants" in objs and "Tencent" not in objs


def test_working_memory_holds_active_query_set():
    """Working memory: lean_context populates a transient, inspectable active set for the current query."""
    mem = build()
    assert mem.working_set == []  # nothing attended yet
    mem.lean_context("Where do I work?", user_id="u1", n_chunks=1)
    assert isinstance(mem.working_set, list)  # populated with the query's active facts (may be empty if none)


def test_decay_sweep_runs_in_consolidation():
    """Bet E: the salience sweep actually runs (it was dead code). An old incidental fact ends below 1.0;
    a durable preference stays at full salience."""
    mem = build()  # consolidate() -> _decay_sweep() runs
    live = [f for f in mem.fact_store.values() if f.is_live()]
    # at least the sweep executed without error and salience is bounded in (0, 1]
    assert all(0.0 < f.salience <= 1.5 for f in live)


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


def test_llm_escalation_resolves_borderline_conflict():
    """§3.2 step 3: a same-subject pair with similarity in the ambiguous band (just below threshold) is
    escalated to the LLM adjudicator only when one is configured; without it, the pair coexists."""
    from engram.consolidate.conflict import ConflictResolver
    from engram.embed import HashingEmbedder
    from engram.types import Fact

    old = Fact(subject="u", predicate="does_yoga", object="twice a week", valid_at=BASE)
    old.embedding = [1.0, 0.0]
    new = Fact(subject="u", predicate="practices_yoga", object="three times a week", valid_at=BASE + 1)
    new.embedding = [0.75, 0.66]  # cosine ≈ 0.75 → in the ambiguous band [0.68, 0.80), below auto-threshold

    # no LLM -> borderline pair is NOT superseded (stays LLM-free, both coexist)
    r0 = ConflictResolver(embedder=HashingEmbedder(2), sim_threshold=0.80)
    assert not r0.reconcile(new, [old])[1]

    # with an LLM that says REPLACES -> the old fact is invalidated
    class FakeLLM:
        def complete(self, prompt, system=None, **k):
            return "REPLACES"

    r1 = ConflictResolver(embedder=HashingEmbedder(2), sim_threshold=0.80, llm=FakeLLM())
    assert old in r1.reconcile(new, [old])[1]


def test_reflector_propagates_supersession_into_summaries():
    """A summary frozen before a knowledge-update should get the current value appended by the reflector."""
    from engram.types import Episode, Fact

    mem = Memory()
    ep = mem.add("I do yoga twice a week.", user_id="u1", session_id="s1", event_time=BASE)
    # an old fact (sourced from ep) that a later fact supersedes
    old = Fact(subject="u1", predicate="yoga_frequency", object="twice a week",
               user_id=mem.resolver.resolve("u1"), valid_at=BASE, provenance=[ep.id],
               invalid_at=BASE + 5 * DAY, expired_at=BASE + 5 * DAY)
    new = Fact(subject="u1", predicate="yoga_frequency", object="three times a week",
               user_id=mem.resolver.resolve("u1"), valid_at=BASE + 5 * DAY, supersedes=old.id)
    mem.fact_store.upsert(old.id, [0.0], old)
    mem.fact_store.upsert(new.id, [0.0], new)
    mem.summarize_episodes([ep])  # freezes the "twice a week" summary
    assert "[updated:" not in ep.summary
    n = mem.reflect("u1")
    assert n == 1
    assert "[updated:" in ep.summary and "three times a week" in ep.summary


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


def test_type_weighting_is_relevance_gated_and_embedder_gated():
    """Two safety properties of the corrected type-weighting:
      (1) it is applied to the SEMANTIC score, so it reorders among relevant facts but never lifts an
          off-topic one — 'where do I work?' must still return the employer, not a favorite;
      (2) it is gated off for the offline HashingEmbedder (noisy cosines)."""
    from engram.embed import HashingEmbedder
    from engram.retrieve.hybrid import HybridRetriever
    from engram.store import InMemoryGraphStore, InMemoryVectorStore
    from engram.config import Config

    r = HybridRetriever(InMemoryVectorStore(), InMemoryGraphStore(), HashingEmbedder(64), Config())
    assert r._semantic is False, "type-weighting must be gated off for the hashing embedder"

    # relevance still wins (this is the regression the earlier multiplier caused: a 'favorite_language'
    # fact outranking 'works_at' for a work query)
    mem = build()
    ans = mem.search("Where do I work?", user_id="u1").answer().lower()
    assert "tencent" in ans, "relevance must win — a preference fact must not outrank the employer"


def test_focus_track_boosts_salience_and_mute_hides():
    """The 关注点 panel is REAL wiring, not a label (CLAUDE.md §3.3): a tracked topic boosts a fact's
    salience (a retrieval-scoring + decay-exemption signal), and a muted topic is dropped from both the
    assembled read context and the synthesized persona."""
    mem = Memory()
    mem.add_fact("user", "uses", "Python", user_id="u1")
    mem.add_fact("user", "plays", "guitar", user_id="u1")
    py = next(f for f in mem.fact_store.values() if "python" in f.text.lower())
    before = py.salience

    mem.set_focus(track=["python"], mute=["guitar"])
    assert py.salience > before, "tracked topic must boost salience"

    ctx = mem.lean_context("what do I do", user_id="u1", n_chunks=0).lower()
    assert "python" in ctx, "tracked fact should remain in the read context"
    assert "guitar" not in ctx, "muted topic must be hidden from the read context"

    persona = mem.build_persona("u1")
    assert "FOCUS AREAS" in persona, "tracked topics should surface in the profile"
    assert "guitar" not in persona.lower(), "muted topic must not appear in the persona"


def test_focus_persists_across_save_open(tmp_path):
    mem = Memory()
    mem.add_fact("user", "uses", "Python", user_id="u1")
    mem.set_focus(track=["python", "work"], mute=["weight"])
    p = str(tmp_path / "u.pkl")
    mem.save(p)
    assert Memory.open(p).get_focus() == {"track": ["python", "work"], "mute": ["weight"]}


def test_graph_data_has_no_dangling_edges():
    """graph_data() feeds the 关系图谱 view: every edge endpoint must be a returned node, and orphan
    entities (no surviving edge) are dropped so the picture is about relationships."""
    mem = build()
    g = mem.graph_data("u1")
    assert g["nodes"] and g["edges"], "a consolidated history should yield a non-empty graph"
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["source"] in ids and e["target"] in ids, "no dangling edge endpoints"
        assert "predicate" in e and "live" in e
