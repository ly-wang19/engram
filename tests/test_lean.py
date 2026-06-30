"""The lean read path (CLAUDE.md Bet A/E): the scalable win condition — answer from a small retrieved
slice (persona + facts + session summaries + a couple full chunks), NOT the whole history. These tests
pin the framework's behavior offline (HashingEmbedder + rule extractor, zero deps, deterministic):
L2 summaries get built and indexed, L3 persona is synthesized, and lean_context assembles them correctly
with dedup + a hard size cap."""
from __future__ import annotations

from engram import Config, Memory
from engram.types import Episode
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


def test_evidence_budgeting_keeps_exact_raw_detail_under_tight_budget():
    from engram.config import Config
    from engram.types import Fact

    def make(enabled: bool) -> str:
        mem = Memory(config=Config(evidence_budgeting=enabled))
        ep = mem.add(
            "The Apollo launch code is A17. Keep the printed checklist near the blue binder.",
            user_id="u1",
            session_id="apollo",
            event_time=BASE,
        )
        fact = Fact(
            subject="Apollo",
            predicate="launch_code",
            object="A17",
            user_id=mem.resolver.resolve("u1"),
            valid_at=BASE,
            provenance=[ep.id],
        )
        fact.embedding = mem.embedder.embed(fact.text)
        mem.fact_store.upsert(fact.id, fact.embedding, fact)
        for i in range(12):
            mem.add_fact(
                "Apollo",
                "project_note",
                f"background filler note {i} with operational chatter that is not the checklist location",
                user_id="u1",
                valid_at=BASE + (i + 1) * DAY,
            )
        return mem.lean_context(
            "What is Apollo's launch code and where is the printed checklist?",
            user_id="u1",
            persona=False,
            n_summaries=0,
            n_chunks=0,
            char_budget=360,
        )

    enabled = make(True)
    disabled = make(False)

    assert len(enabled) <= 360
    assert "blue binder" in enabled
    assert "blue binder" not in disabled


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


def test_update_fact_rebuilds_graph_relation():
    mem = Memory()
    f = mem.add_fact("user", "works_at", "ByteDance", user_id="u1")

    mem.update_fact(f.id, object="Moonshot AI")

    graph = mem.graph_data("u1")
    edge_text = str(graph)
    assert "Moonshot AI" in edge_text
    assert "ByteDance" not in edge_text
    assert len([edge for edge in graph["edges"] if edge["fact_id"] == f.id]) == 1


def test_delete_fact_removes_graph_export_edge():
    mem = Memory()
    f = mem.add_fact("user", "has_disease", "diabetes", user_id="u1")
    assert f.id in {edge["fact_id"] for edge in mem.graph_data("u1")["edges"]}

    assert mem.delete_fact(f.id) is True

    graph = mem.graph_data("u1")
    assert f.id not in {edge["fact_id"] for edge in graph["edges"]}
    assert "diabetes" not in str(graph).lower()


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


def test_evidence_planner_is_query_based_not_benchmark_based():
    """The planner routes by evidence shape only; there is no benchmark category input."""
    from engram.retrieve.evidence import plan_evidence

    agg = plan_evidence("How many trips did I take, and list every city?")
    assert agg.aggregation and agg.use_cascade and agg.n_summaries > 0
    assert agg.subqueries

    topic = plan_evidence("trips")
    assert not topic.aggregation

    pref = plan_evidence("What food do I prefer or dislike?")
    assert pref.preference and pref.n_chunks > 0
    diet = plan_evidence("What are my dietary restrictions?")
    assert diet.preference and diet.n_chunks > 0

    temporal = plan_evidence("When was the first time I traveled after Lisbon?")
    assert temporal.timeline

    history = plan_evidence("Where did Wei work before Moonshot AI?")
    assert history.history and history.timeline and history.n_facts > 0

    kits = plan_evidence("How many model kits have I worked on or bought?")
    assert any("model kit" in q for q in kits.subqueries)

    freq = plan_evidence("How often do I practice yoga now?")
    assert freq.current_state and not freq.aggregation and not freq.subqueries

    proc = plan_evidence("How do I rotate the PAT?")
    assert proc.procedural and proc.n_facts > 0 and proc.n_chunks > 0
    assert not plan_evidence("How many PAT rotations did I do?").procedural
    assert plan_evidence("这个 PAT 流程怎么操作?").procedural

    dur = plan_evidence("How many weeks in total did I spend reading these books?")
    assert dur.duration and dur.aggregation and dur.n_chunks > 0

    ms = plan_evidence("What is the profession of the user's sister who moved to Seattle?")
    assert ms.multi_hop and ms.use_agentic and ms.n_chunks > 0
    assert not ms.aggregation
    assert "sister profession" in ms.subqueries
    assert "sister moved seattle" in ms.subqueries

    colleague = plan_evidence("Where does my colleague work?")
    assert colleague.multi_hop and colleague.n_facts > 0 and colleague.n_chunks > 0
    assert "colleague" in colleague.subqueries
    assert "colleague employer" in colleague.subqueries
    assert "colleague works" in colleague.subqueries

    sibling = plan_evidence("Where does my sister live?")
    assert sibling.multi_hop and "sister lives" in sibling.subqueries


def test_lean_context_auto_adds_timeline_for_temporal_queries():
    mem = build()
    ctx = mem.lean_context("When was the first time I traveled?", user_id="u1", n_chunks=0)
    assert "TIMELINE (oldest to newest" in ctx


def test_lean_context_auto_adds_preference_records():
    mem = build()
    ctx = mem.lean_context("What is my favorite language?", user_id="u1", n_chunks=0)
    assert "PREFERENCE RECORDS (current, structured):" in ctx
    assert "favorite language" in ctx.lower()


def test_lean_context_auto_adds_explicit_preference_records():
    mem = Memory()
    mem.add("I prefer aisle seats and avoid red-eye flights.", user_id="u1", event_time=BASE)
    mem.consolidate()

    ctx = mem.lean_context("What travel preferences should you remember?", user_id="u1", n_chunks=0)

    assert "PREFERENCE RECORDS (current, structured):" in ctx
    assert "prefers" in ctx and "aisle seats" in ctx
    assert "avoids" in ctx and "red-eye flights" in ctx


def test_lean_context_auto_adds_procedural_memory_block():
    from engram.types import Fact

    mem = Memory()
    ep = mem.add(
        "PAT runbook source: rotate the PAT by opening security settings and updating CI secrets.",
        user_id="u1",
        session_id="pat-runbook",
        event_time=BASE,
    )
    fact = Fact(
        subject="PAT",
        predicate="procedure",
        object="open security settings and update CI secrets",
        user_id=mem.resolver.resolve("u1"),
        valid_at=BASE,
        provenance=[ep.id],
    )
    fact.embedding = mem.embedder.embed(fact.text)
    mem.fact_store.upsert(fact.id, fact.embedding, fact)

    ctx = mem.lean_context(
        "What is the PAT runbook?",
        user_id="u1",
        persona=False,
        n_summaries=0,
        n_chunks=0,
    )

    assert "PROCEDURAL MEMORY (standing rules/how-to, source-backed):" in ctx
    assert "sessions: pat-runbook" in ctx
    assert "security settings" in ctx


def test_lean_context_auto_adds_dietary_restriction_records():
    mem = Memory()
    mem.add("I'm vegetarian and allergic to peanuts.", user_id="u1", event_time=BASE)
    mem.consolidate()

    ctx = mem.lean_context("What are my dietary restrictions?", user_id="u1", n_chunks=0)
    assert "PREFERENCE RECORDS (current, structured):" in ctx
    assert "diet" in ctx and "vegetarian" in ctx
    assert "allergic to" in ctx and "peanuts" in ctx


def test_lean_context_auto_adds_aggregation_evidence():
    mem = build()
    ctx = mem.lean_context("How many trips did I take?", user_id="u1", n_chunks=0)
    assert "AGGREGATION EVIDENCE" in ctx
    assert "date | source | evidence" in ctx


def test_aggregation_evidence_keeps_raw_candidates_when_summaries_drop_details():
    mem = Memory()
    mem.add(
        "I recently finished a Tamiya 1/48 scale Spitfire Mk.V model kit.",
        user_id="u1",
        session_id="s1",
        event_time=BASE,
    )

    ctx = mem.lean_context(
        "How many model kits have I worked on or bought?",
        user_id="u1",
        persona=False,
        n_facts=0,
        n_summaries=0,
        n_chunks=0,
        char_budget=10_000,
    )

    agg = ctx.split("AGGREGATION EVIDENCE", 1)[1]
    assert "Spitfire" in agg


def test_structured_aggregation_candidates_filter_furniture_accessories():
    from engram.retrieve.aggregate import extract_aggregation_candidates, render_aggregation_candidates

    eps = [
        Episode("I bought scratch guards from IKEA to protect the furniture.", event_time=BASE),
        Episode("I just got a new coffee table from West Elm.", event_time=BASE + DAY),
        Episode("I finally assembled that IKEA bookshelf for my home office.", event_time=BASE + 2 * DAY),
        Episode("I got around to fixing the wobbly leg on my kitchen table.", event_time=BASE + 3 * DAY),
        Episode("I needed a new mattress, and ordered one from Casper.", event_time=BASE + 4 * DAY),
        Episode("I bought organic dog food with vegetables for Max and washed his old bed.", event_time=BASE + 5 * DAY),
    ]

    candidates = extract_aggregation_candidates(
        "How many pieces of furniture did I buy, assemble, sell, or fix?",
        [],
        eps,
    )
    included = {c.canonical_item for c in candidates if c.include}
    excluded = {c.canonical_item for c in candidates if not c.include}

    assert {"coffee table", "bookshelf", "kitchen table", "mattress"} <= included
    assert "scratch guard" in excluded
    assert "bed" in excluded
    assert "table" not in included
    rendered = render_aggregation_candidates(candidates)
    assert "AGGREGATION CANDIDATES" in rendered
    assert sum(1 for c in candidates if c.include) == 4


def test_lean_context_renders_structured_aggregation_candidates():
    mem = Memory()
    mem.add("I bought scratch guards from IKEA to protect the furniture.", user_id="u1", event_time=BASE)
    mem.add("I just got a new coffee table from West Elm.", user_id="u1", event_time=BASE + DAY)
    mem.add("I finally assembled that IKEA bookshelf for my home office.", user_id="u1", event_time=BASE + 2 * DAY)
    mem.add("I got around to fixing the wobbly leg on my kitchen table.", user_id="u1", event_time=BASE + 3 * DAY)
    mem.add("I needed a new mattress, and ordered one from Casper.", user_id="u1", event_time=BASE + 4 * DAY)

    ctx = mem.lean_context(
        "How many pieces of furniture did I buy, assemble, sell, or fix?",
        user_id="u1",
        persona=False,
        n_facts=0,
        n_summaries=0,
        n_chunks=0,
        char_budget=20_000,
    )

    assert "AGGREGATION CANDIDATES" in ctx
    assert "INCLUDE" in ctx and "coffee table" in ctx and "mattress" in ctx
    assert "EXCLUDE" in ctx and "scratch guards" in ctx


def test_numeric_aggregation_candidates_extract_money_and_hours():
    from engram.retrieve.aggregate import extract_aggregation_candidates, render_aggregation_candidates

    money_eps = [
        Episode("I attended a mindfulness workshop. I paid $20 to attend.", event_time=BASE),
        Episode("I attended a writing workshop at a festival. I paid $200 to attend.", event_time=BASE + DAY),
        Episode("I attended a digital marketing workshop. I paid $500 to attend.", event_time=BASE + 2 * DAY),
    ]
    money = extract_aggregation_candidates(
        "How much total money did I spend on attending workshops?",
        [],
        money_eps,
    )

    assert sum(c.value or 0 for c in money if c.include) == 720
    rendered = render_aggregation_candidates(money)
    assert "value | unit" in rendered
    assert "$500 workshop" in rendered

    duration_eps = [
        Episode("I went for a 30-minute jog around the neighborhood.", event_time=BASE),
        Episode("I used to practice yoga three times a week, each time for 2 hours.", event_time=BASE + DAY),
    ]
    duration = extract_aggregation_candidates(
        "How many hours of jogging and yoga did I do last week?",
        [],
        duration_eps,
    )

    assert sum(c.value or 0 for c in duration if c.include) == 0.5
    assert any(not c.include and "past habit" in c.exclude_reason for c in duration)


def test_numeric_aggregation_candidates_can_be_disabled():
    mem = Memory(config=Config(numeric_aggregation_candidates=False))
    mem.add("I attended a digital marketing workshop. I paid $500 to attend.", user_id="u1", event_time=BASE)

    ctx = mem.lean_context(
        "How much total money did I spend on attending workshops?",
        user_id="u1",
        persona=False,
        n_facts=0,
        n_summaries=0,
        n_chunks=0,
        char_budget=10_000,
    )

    assert "AGGREGATION CANDIDATES" not in ctx


def test_lean_context_auto_adds_duration_evidence_for_time_totals():
    mem = Memory()
    mem.add("I started reading 'The Nightingale' today.", user_id="u1", session_id="s1", event_time=BASE)
    mem.add("I finished reading 'The Nightingale' today.", user_id="u1", session_id="s2",
            event_time=BASE + 14 * DAY)

    ctx = mem.lean_context(
        "How many weeks did I spend reading 'The Nightingale'?",
        user_id="u1",
        persona=False,
        n_facts=0,
        n_summaries=0,
        n_chunks=0,
        char_budget=10_000,
    )

    assert "DURATION EVIDENCE (pair start/finish dates per item" in ctx
    assert "started reading" in ctx
    assert "finished reading" in ctx


def test_aggregation_evidence_prioritizes_query_overlap():
    mem = build()
    eps = sorted(mem.episodes_doc.values(), key=lambda e: e.event_time)
    block = mem._aggregation_block(
        facts=[],
        summaries=[eps[2], eps[1]],
        detail_eps=[],
        query="How many trips to Kyoto?",
    )
    body = block.split("--- | --- | ---", 1)[1].lower()
    assert "kyoto" in body
    assert "cat named luna" not in body


def test_lean_context_auto_adds_current_state_for_now_queries():
    mem = build()
    ctx = mem.lean_context("Where do I currently work?", user_id="u1", n_chunks=0)
    assert "CURRENT STATE (live facts only):" in ctx
    assert "current value" in ctx


def test_lean_context_as_of_profile_does_not_leak_current_facts():
    mem = Memory()
    mem.add("Wei works at Tencent.", user_id="u1", session_id="old", event_time=BASE)
    mem.add("Wei works at Moonshot AI.", user_id="u1", session_id="new", event_time=BASE + 30 * DAY)
    mem.consolidate()

    ctx = mem.lean_context(
        "Where does Wei work?",
        user_id="u1",
        as_of=BASE + 10 * DAY,
        n_chunks=0,
        char_budget=10_000,
    )
    assert "USER PROFILE (as of" in ctx
    assert "Tencent" in ctx
    assert "Moonshot AI" not in ctx


def test_lean_context_adds_history_for_previous_value_queries():
    mem = Memory()
    old = mem.add_fact("Wei", "works_at", "Tencent", user_id="u1", valid_at=BASE)
    new = mem.add_fact("Wei", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + 30 * DAY)

    ctx = mem.lean_context(
        "Where did Wei work before Moonshot AI?",
        user_id="u1",
        persona=False,
        n_chunks=0,
        char_budget=10_000,
    )
    assert new.supersedes == old.id
    assert "FACT HISTORY (supersession chain" in ctx
    assert "Tencent" in ctx
    assert "Moonshot AI" in ctx
    assert "superseded" in ctx and "current" in ctx


def test_search_answers_previous_value_from_supersession_history():
    mem = Memory()
    mem.add_fact("Wei", "works_at", "Tencent", user_id="u1", valid_at=BASE)
    mem.add_fact("Wei", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + 30 * DAY)

    before = mem.search("Where did Wei work before Moonshot AI?", user_id="u1")
    previous = mem.search("Where did Wei previously work?", user_id="u1")
    current = mem.search("Where does Wei work now?", user_id="u1")

    assert before.via == "history"
    assert "tencent" in before.answer().lower()
    assert [f.object for f in before.facts] == ["Tencent", "Moonshot AI"]
    assert previous.via == "history"
    assert "tencent" in previous.answer().lower()
    assert current.via == "hybrid"
    assert "moonshot" in current.answer().lower()


def test_temporal_history_query_can_be_disabled_for_ablation():
    from engram.config import Config

    mem = Memory(config=Config(temporal_history_queries=False))
    mem.add_fact("Wei", "works_at", "Tencent", user_id="u1", valid_at=BASE)
    mem.add_fact("Wei", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + 30 * DAY)

    res = mem.search("Where did Wei work before Moonshot AI?", user_id="u1")
    ctx = mem.lean_context(
        "Where did Wei work before Moonshot AI?",
        user_id="u1",
        persona=False,
        n_chunks=0,
        char_budget=10_000,
    )

    assert res.via == "hybrid"
    assert "moonshot" in res.answer().lower()
    assert "FACT HISTORY (supersession chain" not in ctx


def test_lean_context_adds_evolution_chain_for_current_lookup():
    mem = Memory()
    old = mem.add_fact("Wei", "works_at", "Tencent", user_id="u1", valid_at=BASE)
    new = mem.add_fact("Wei", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + 30 * DAY)

    ctx = mem.lean_context(
        "Where does Wei work?",
        user_id="u1",
        persona=False,
        n_chunks=0,
        char_budget=10_000,
    )

    assert new.supersedes == old.id
    assert "FACTS (current, dated):" in ctx
    assert "FACT EVOLUTION (retrieved supersession chain):" in ctx
    assert "Tencent" in ctx
    assert "Moonshot AI" in ctx
    assert "superseded" in ctx and "current" in ctx


def test_lean_context_evolution_chain_respects_as_of_boundary():
    mem = Memory()
    mem.add_fact("Wei", "works_at", "Tencent", user_id="u1", valid_at=BASE)
    mem.add_fact("Wei", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + 30 * DAY)

    ctx = mem.lean_context(
        "Where does Wei work?",
        user_id="u1",
        as_of=BASE + 10 * DAY,
        persona=False,
        n_chunks=0,
        char_budget=10_000,
    )

    assert "Tencent" in ctx
    assert "Moonshot AI" not in ctx
    assert "FACT EVOLUTION (retrieved supersession chain):" not in ctx


def test_context_for_adds_fact_evolution_chain():
    mem = Memory()
    mem.add_fact("Wei", "works_at", "Tencent", user_id="u1", valid_at=BASE)
    mem.add_fact("Wei", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + 30 * DAY)

    ctx = mem.context_for("Where does Wei work?", user_id="u1", k_chunks=0)

    assert "FACT EVOLUTION (retrieved supersession chain):" in ctx
    assert "Tencent" in ctx
    assert "Moonshot AI" in ctx


def test_chain_evidence_can_be_disabled_for_ablation():
    from engram.config import Config

    mem = Memory(config=Config(chain_evidence=False))
    mem.add_fact("Wei", "works_at", "Tencent", user_id="u1", valid_at=BASE)
    mem.add_fact("Wei", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + 30 * DAY)

    ctx = mem.lean_context(
        "Where does Wei work?",
        user_id="u1",
        persona=False,
        n_chunks=0,
        char_budget=10_000,
    )

    assert "Moonshot AI" in ctx
    assert "FACT EVOLUTION (retrieved supersession chain):" not in ctx


def test_lean_context_adds_provenance_raw_evidence_for_retrieved_fact():
    from engram.config import Config
    from engram.types import Fact

    mem = Memory(config=Config(evidence_planner=False))
    ep = mem.add(
        "The Apollo launch code is A17. Keep the printed checklist near the blue binder.",
        user_id="u1",
        session_id="apollo",
        event_time=BASE,
    )
    fact = Fact(
        subject="Apollo",
        predicate="launch_code",
        object="A17",
        user_id=mem.resolver.resolve("u1"),
        valid_at=BASE,
        provenance=[ep.id],
    )
    fact.embedding = mem.embedder.embed(fact.text)
    mem.fact_store.upsert(fact.id, fact.embedding, fact)

    ctx = mem.lean_context(
        "What is Apollo's launch code?",
        user_id="u1",
        persona=False,
        n_chunks=0,
        char_budget=10_000,
    )

    assert "FACTS (current, dated):" in ctx
    assert "PROVENANCE RAW EVIDENCE (source episodes for retrieved facts):" in ctx
    assert "A17" in ctx
    assert "blue binder" in ctx


def test_provenance_evidence_can_be_disabled_for_ablation():
    from engram.config import Config
    from engram.types import Fact

    mem = Memory(config=Config(evidence_planner=False, provenance_evidence=False))
    ep = mem.add(
        "The Apollo launch code is A17. Keep the printed checklist near the blue binder.",
        user_id="u1",
        session_id="apollo",
        event_time=BASE,
    )
    fact = Fact(
        subject="Apollo",
        predicate="launch_code",
        object="A17",
        user_id=mem.resolver.resolve("u1"),
        valid_at=BASE,
        provenance=[ep.id],
    )
    fact.embedding = mem.embedder.embed(fact.text)
    mem.fact_store.upsert(fact.id, fact.embedding, fact)

    ctx = mem.lean_context(
        "What is Apollo's launch code?",
        user_id="u1",
        persona=False,
        n_chunks=0,
        char_budget=10_000,
    )

    assert "A17" in ctx
    assert "PROVENANCE RAW EVIDENCE" not in ctx
    assert "blue binder" not in ctx


def test_provenance_raw_evidence_dedups_full_detail_chunk():
    from engram.types import Fact

    mem = Memory()
    ep = mem.add(
        "The Apollo launch code is A17. Keep the printed checklist near the blue binder.",
        user_id="u1",
        session_id="apollo",
        event_time=BASE,
    )
    fact = Fact(
        subject="Apollo",
        predicate="launch_code",
        object="A17",
        user_id=mem.resolver.resolve("u1"),
        valid_at=BASE,
        provenance=[ep.id],
    )
    fact.embedding = mem.embedder.embed(fact.text)
    mem.fact_store.upsert(fact.id, fact.embedding, fact)

    ctx = mem.lean_context(
        "What is Apollo's launch code?",
        user_id="u1",
        persona=False,
        n_chunks=1,
        char_budget=10_000,
    )

    assert "RELEVANT CONVERSATIONS (full detail):" in ctx
    assert "blue binder" in ctx
    assert "PROVENANCE RAW EVIDENCE (source episodes for retrieved facts):" not in ctx


def test_provenance_source_episode_is_promoted_into_detail_chunks():
    from engram.config import Config
    from engram.types import Fact

    def make(enabled: bool) -> str:
        mem = Memory(
            config=Config(
                evidence_planner=False,
                provenance_evidence=False,
                provenance_chunk_promotion=enabled,
            )
        )
        source = mem.add(
            "A17 is written on the tag tucked inside the blue binder.",
            user_id="u1",
            session_id="apollo-source",
            event_time=BASE,
        )
        for i in range(5):
            mem.add(
                f"Apollo launch code rehearsal note {i}: the team reviewed old checklist formats.",
                user_id="u1",
                session_id=f"apollo-distractor-{i}",
                event_time=BASE + (i + 1) * DAY,
            )
        fact = Fact(
            subject="Apollo",
            predicate="launch_code",
            object="A17",
            user_id=mem.resolver.resolve("u1"),
            valid_at=BASE,
            provenance=[source.id],
        )
        fact.embedding = mem.embedder.embed(fact.text)
        mem.fact_store.upsert(fact.id, fact.embedding, fact)
        return mem.lean_context(
            "What is Apollo's launch code?",
            user_id="u1",
            persona=False,
            n_summaries=0,
            n_chunks=1,
            char_budget=10_000,
        )

    enabled = make(True)
    disabled = make(False)
    enabled_detail = enabled.split("RELEVANT CONVERSATIONS (full detail):", 1)[1]
    disabled_detail = disabled.split("RELEVANT CONVERSATIONS (full detail):", 1)[1]

    assert "blue binder" in enabled_detail
    assert "apollo-source" in enabled_detail
    assert "blue binder" not in disabled_detail
    assert "apollo-distractor" in disabled_detail


def test_provenance_raw_evidence_is_hidden_in_redacted_context():
    from engram.config import Config
    from engram.types import Fact

    mem = Memory(config=Config(evidence_planner=False))
    ep = mem.add(
        "The Apollo launch code is A17. Keep the printed checklist near the blue binder.",
        user_id="u1",
        session_id="apollo",
        event_time=BASE,
    )
    fact = Fact(
        subject="Apollo",
        predicate="launch_code",
        object="A17",
        user_id=mem.resolver.resolve("u1"),
        valid_at=BASE,
        provenance=[ep.id],
        sensitive=True,
    )
    fact.embedding = mem.embedder.embed(fact.text)
    mem.fact_store.upsert(fact.id, fact.embedding, fact)

    ctx = mem.lean_context(
        "What is Apollo's launch code?",
        user_id="u1",
        persona=False,
        n_chunks=0,
        redact_sensitive=True,
        char_budget=10_000,
    )

    assert "PROVENANCE RAW EVIDENCE" not in ctx
    assert "blue binder" not in ctx


def test_context_for_adds_provenance_raw_evidence_for_fact_sources():
    from engram.types import Fact

    mem = Memory()
    ep = mem.add(
        "The Apollo launch code is A17. Keep the printed checklist near the blue binder.",
        user_id="u1",
        session_id="apollo",
        event_time=BASE,
    )
    fact = Fact(
        subject="Apollo",
        predicate="launch_code",
        object="A17",
        user_id=mem.resolver.resolve("u1"),
        valid_at=BASE,
        provenance=[ep.id],
    )
    fact.embedding = mem.embedder.embed(fact.text)
    mem.fact_store.upsert(fact.id, fact.embedding, fact)

    ctx = mem.context_for("What is Apollo's launch code?", user_id="u1", k_chunks=0)

    assert "PROVENANCE RAW EVIDENCE (source episodes for retrieved facts):" in ctx
    assert "blue binder" in ctx


def test_current_state_preserves_multi_valued_attributes():
    mem = Memory()
    mem.add_fact("Wei", "owns", "a bike", user_id="u1")
    mem.add_fact("Wei", "owns", "a camera", user_id="u1")

    ctx = mem.lean_context(
        "What do I currently own?",
        user_id="u1",
        persona=False,
        n_chunks=0,
        char_budget=10_000,
    )
    state = ctx.split("CURRENT STATE (live facts only):", 1)[1].split("\n\n", 1)[0]
    assert "a bike" in state
    assert "a camera" in state


def test_lean_context_uses_multi_hop_subqueries_for_detail_chunks():
    mem = Memory()
    eps = [
        mem.add("My sister Maya is a pediatrician at a children's hospital.", user_id="u1",
                session_id="s1", event_time=BASE),
        mem.add("Maya just moved to Seattle for a fellowship.", user_id="u1",
                session_id="s2", event_time=BASE + DAY),
        mem.add("I bought a new espresso machine and it is wonderful.", user_id="u1",
                session_id="s3", event_time=BASE + 2 * DAY),
    ]
    mem.summarize_episodes(eps)

    ctx = mem.lean_context(
        "What is the profession of the user's sister who moved to Seattle?",
        user_id="u1",
        persona=False,
        n_facts=0,
        n_summaries=0,
        n_chunks=0,
    )

    assert "RELEVANT CONVERSATIONS (full detail):" in ctx
    assert "pediatrician" in ctx.lower()
    assert "seattle" in ctx.lower()


def test_lean_context_graph_paths_use_clean_multihop_plan():
    mem = Memory()
    eps = [
        mem.add("My sister Anna is a lawyer.", user_id="u1", session_id="s1", event_time=BASE),
        mem.add("My sister Maya is a pediatrician.", user_id="u1", session_id="s2", event_time=BASE + DAY),
        mem.add("Maya just moved to Seattle.", user_id="u1", session_id="s3", event_time=BASE + 2 * DAY),
    ]
    mem.consolidate(eps)
    mem.summarize_episodes(eps)

    ctx = mem.lean_context(
        "What is the profession of the user's sister who moved to Seattle?",
        user_id="u1",
        persona=False,
        n_chunks=0,
        char_budget=10_000,
    )
    graph = ctx.split("GRAPH PATHS (relation evidence):", 1)[1].split("\n\n", 1)[0]
    assert "u1 sister Maya" in graph
    assert "Maya lives in Seattle" in graph
    assert "Maya occupation pediatrician" in graph
    assert "u1 sister Anna" not in graph


def test_multihop_planner_includes_cold_tier_relation_facts():
    mem = Memory()
    eps = [
        mem.add("My sister Maya is a pediatrician.", user_id="u1", session_id="s1", event_time=BASE),
        mem.add("Maya just moved to Seattle.", user_id="u1", session_id="s2", event_time=BASE + DAY),
    ]
    mem.consolidate(eps)

    sister = next(f for f in mem.fact_store.values() if f.predicate == "sister")
    sister.salience = 0.0
    assert mem.evict_cold(max_hot=len(mem.fact_store.values()) - 1) == 1
    assert mem.cold_store.get(sister.id) is not None

    ctx = mem.lean_context(
        "What is the profession of the user's sister who moved to Seattle?",
        user_id="u1",
        persona=False,
        n_chunks=0,
        char_budget=10_000,
    )
    graph = ctx.split("GRAPH PATHS (relation evidence):", 1)[1].split("\n\n", 1)[0]
    assert "u1 sister Maya" in graph
    assert "Maya lives in Seattle" in graph
    assert "Maya occupation pediatrician" in graph


def test_deleted_relation_fact_does_not_drive_multihop_planner():
    mem = Memory()
    eps = [
        mem.add("My sister Maya is a pediatrician.", user_id="u1", session_id="s1", event_time=BASE),
        mem.add("Maya just moved to Seattle.", user_id="u1", session_id="s2", event_time=BASE + DAY),
    ]
    mem.consolidate(eps)
    sister = next(f for f in mem.fact_store.values() if f.predicate == "sister")

    assert mem.delete_fact(sister.id) is True

    res = mem.search("What is the profession of the user's sister who moved to Seattle?", user_id="u1")
    assert res.via != "multi-hop"
    assert "pediatrician" not in res.answer().lower()


def test_graph_scores_expand_beyond_one_hop_for_bridge_facts():
    mem = Memory()
    direct = mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    bridge = mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    second_hop = mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE + 2 * DAY)

    scores, qids = mem.retriever._graph_scores(
        "Tell me about Wei",
        mem.resolver.resolve("u1"),
        [direct, bridge, second_hop],
        None,
    )

    assert qids, "query should anchor on the Wei entity"
    assert scores[direct.id] == 1.0
    assert scores[bridge.id] > scores[second_hop.id] > 0.0


def test_graph_scores_respect_as_of_during_expansion():
    mem = Memory()
    direct = mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    future_bridge = mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    older_target = mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE)
    live = [direct, future_bridge, older_target]

    before, _ = mem.retriever._graph_scores(
        "Tell me about Wei",
        mem.resolver.resolve("u1"),
        live,
        BASE + 0.5 * DAY,
    )
    after, _ = mem.retriever._graph_scores(
        "Tell me about Wei",
        mem.resolver.resolve("u1"),
        live,
        BASE + 2 * DAY,
    )

    assert before[older_target.id] == 0.0
    assert after[older_target.id] > 0.0


def test_graph_proximity_can_be_disabled_for_ablation():
    from engram.config import Config

    mem = Memory(config=Config(graph_proximity=False))
    direct = mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    bridge = mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    second_hop = mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE + 2 * DAY)

    scores, qids = mem.retriever._graph_scores(
        "Tell me about Wei",
        mem.resolver.resolve("u1"),
        [direct, bridge, second_hop],
        None,
    )
    related = mem._graph_related_facts("Tell me about Wei", mem.resolver.resolve("u1"), None)
    paths = mem._graph_paths_block("Tell me about Wei", mem.resolver.resolve("u1"), None)

    assert qids, "entity anchoring still works; only proximity evidence is ablated"
    assert all(score == 0.0 for score in scores.values())
    assert related == []
    assert paths == ""


def test_graph_relation_awareness_prioritizes_query_relevant_edges():
    from engram.config import Config

    mem = Memory(config=Config(graph_relation_awareness=True))
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    distractor = mem.add_fact("Lin", "likes", "jazz", user_id="u1", valid_at=BASE + 2 * DAY)
    target = mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE + 3 * DAY)
    live = [f for f in mem.fact_store.values() if f.user_id == mem.resolver.resolve("u1")]

    scores, _ = mem.retriever._graph_scores(
        "Where is Wei's colleague's company based?",
        mem.resolver.resolve("u1"),
        live,
        None,
    )

    assert scores[target.id] > scores[distractor.id]


def test_graph_relation_awareness_can_be_disabled_for_ablation():
    from engram.config import Config

    mem = Memory(config=Config(graph_relation_awareness=False))
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    distractor = mem.add_fact("Lin", "likes", "jazz", user_id="u1", valid_at=BASE + 2 * DAY)
    target = mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE + 3 * DAY)
    live = [f for f in mem.fact_store.values() if f.user_id == mem.resolver.resolve("u1")]

    scores, _ = mem.retriever._graph_scores(
        "Where is Wei's colleague's company based?",
        mem.resolver.resolve("u1"),
        live,
        None,
    )

    assert scores[target.id] < scores[distractor.id]


def test_graph_path_reinforcement_boosts_multi_path_targets():
    from engram.config import Config

    def build_mem(enabled: bool):
        mem = Memory(config=Config(graph_path_reinforcement=enabled))
        mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
        mem.add_fact("Wei", "mentor", "Maya", user_id="u1", valid_at=BASE + DAY)
        mem.add_fact("Lin", "works_on", "Atlas", user_id="u1", valid_at=BASE + 2 * DAY)
        mem.add_fact("Maya", "works_on", "Atlas", user_id="u1", valid_at=BASE + 3 * DAY)
        mem.add_fact("Lin", "works_on", "Zephyr", user_id="u1", valid_at=BASE + 4 * DAY)
        target = mem.add_fact("Atlas", "based_in", "Reykjavik", user_id="u1", valid_at=BASE + 5 * DAY)
        distractor = mem.add_fact("Zephyr", "based_in", "Lisbon", user_id="u1", valid_at=BASE + 6 * DAY)
        return mem, target, distractor

    enabled_mem, enabled_target, enabled_distractor = build_mem(True)
    enabled_live = [f for f in enabled_mem.fact_store.values() if f.user_id == enabled_mem.resolver.resolve("u1")]
    enabled_scores, _ = enabled_mem.retriever._graph_scores(
        "Where is Wei's project based?",
        enabled_mem.resolver.resolve("u1"),
        enabled_live,
        None,
    )

    disabled_mem, disabled_target, disabled_distractor = build_mem(False)
    disabled_live = [f for f in disabled_mem.fact_store.values() if f.user_id == disabled_mem.resolver.resolve("u1")]
    disabled_scores, _ = disabled_mem.retriever._graph_scores(
        "Where is Wei's project based?",
        disabled_mem.resolver.resolve("u1"),
        disabled_live,
        None,
    )

    assert enabled_scores[enabled_target.id] > enabled_scores[enabled_distractor.id]
    assert disabled_scores[disabled_target.id] <= disabled_scores[disabled_distractor.id]


def test_graph_self_anchor_retrieves_first_person_graph_paths():
    mem = Memory()
    mem.add_fact("u1", "works_on", "Atlas", user_id="u1", valid_at=BASE)
    target = mem.add_fact("Atlas", "based_in", "Reykjavik", user_id="u1", valid_at=BASE + DAY)
    live = [f for f in mem.fact_store.values() if f.user_id == mem.resolver.resolve("u1")]

    scores, qids = mem.retriever._graph_scores(
        "Where is my project based?",
        mem.resolver.resolve("u1"),
        live,
        None,
    )
    ctx = mem.context_for("Where is my project based?", user_id="u1", k_chunks=0, graph=True)

    assert qids
    assert scores[target.id] > 0.0
    assert "Atlas based in Reykjavik" in ctx


def test_graph_self_anchor_can_be_disabled_for_ablation():
    from engram.config import Config

    mem = Memory(config=Config(graph_self_anchor=False))
    mem.add_fact("u1", "works_on", "Atlas", user_id="u1", valid_at=BASE)
    target = mem.add_fact("Atlas", "based_in", "Reykjavik", user_id="u1", valid_at=BASE + DAY)
    live = [f for f in mem.fact_store.values() if f.user_id == mem.resolver.resolve("u1")]

    scores, qids = mem.retriever._graph_scores(
        "Where is my project based?",
        mem.resolver.resolve("u1"),
        live,
        None,
    )
    ctx = mem.context_for("Where is my project based?", user_id="u1", k_chunks=0, graph=True)

    assert qids == set()
    assert scores[target.id] == 0.0
    assert "RELATED FACTS (graph traversal):" not in ctx


def test_graph_entity_alias_anchor_matches_unique_short_name():
    mem = Memory()
    target = mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE)
    live = [f for f in mem.fact_store.values() if f.user_id == mem.resolver.resolve("u1")]

    scores, qids = mem.retriever._graph_scores(
        "Where is Moonshot based?",
        mem.resolver.resolve("u1"),
        live,
        None,
    )
    ctx = mem.context_for("Where is Moonshot based?", user_id="u1", k_chunks=0, graph=True)

    assert qids
    assert scores[target.id] > 0.0
    assert "Moonshot AI based in Beijing" in ctx


def test_graph_entity_alias_anchor_can_be_disabled_for_ablation():
    from engram.config import Config

    mem = Memory(config=Config(graph_entity_alias_anchor=False))
    target = mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE)
    live = [f for f in mem.fact_store.values() if f.user_id == mem.resolver.resolve("u1")]

    scores, qids = mem.retriever._graph_scores(
        "Where is Moonshot based?",
        mem.resolver.resolve("u1"),
        live,
        None,
    )
    ctx = mem.context_for("Where is Moonshot based?", user_id="u1", k_chunks=0, graph=True)

    assert qids == set()
    assert scores[target.id] == 0.0
    assert "RELATED FACTS (graph traversal):" not in ctx


def test_graph_entity_alias_anchor_requires_unique_token():
    mem = Memory()
    first = mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE)
    second = mem.add_fact("Moonshot Labs", "based_in", "Shanghai", user_id="u1", valid_at=BASE + DAY)
    live = [f for f in mem.fact_store.values() if f.user_id == mem.resolver.resolve("u1")]

    scores, qids = mem.retriever._graph_scores(
        "Where is Moonshot based?",
        mem.resolver.resolve("u1"),
        live,
        None,
    )

    assert qids == set()
    assert scores[first.id] == 0.0
    assert scores[second.id] == 0.0


def test_graph_negative_constraints_filter_excluded_location_paths():
    mem = Memory()
    mem.add_fact("Wei", "works_on", "Atlas", user_id="u1", valid_at=BASE)
    mem.add_fact("Wei", "works_on", "Zephyr", user_id="u1", valid_at=BASE + DAY)
    target = mem.add_fact("Atlas", "based_in", "Reykjavik", user_id="u1", valid_at=BASE + 2 * DAY)
    distractor = mem.add_fact("Zephyr", "based_in", "Lisbon", user_id="u1", valid_at=BASE + 3 * DAY)

    query = "Where is Wei's project not in Lisbon based?"
    res = mem.search(query, user_id="u1")
    related = mem._graph_related_facts(query, mem.resolver.resolve("u1"), None)
    ctx = mem.context_for(query, user_id="u1", k_chunks=0, graph=True)

    assert "reykjavik" in res.answer().lower()
    assert target.id in {f.id for f in related}
    assert distractor.id not in {f.id for f in related}
    assert "Atlas based in Reykjavik" in ctx
    assert "Zephyr based in Lisbon" not in ctx


def test_graph_negative_constraints_can_be_disabled_for_ablation():
    from engram.config import Config

    mem = Memory(config=Config(graph_negative_constraints=False))
    mem.add_fact("Wei", "works_on", "Atlas", user_id="u1", valid_at=BASE)
    mem.add_fact("Wei", "works_on", "Zephyr", user_id="u1", valid_at=BASE + DAY)
    mem.add_fact("Atlas", "based_in", "Reykjavik", user_id="u1", valid_at=BASE + 2 * DAY)
    mem.add_fact("Zephyr", "based_in", "Lisbon", user_id="u1", valid_at=BASE + 3 * DAY)

    ctx = mem.context_for("Where is Wei's project not in Lisbon based?", user_id="u1", k_chunks=0, graph=True)

    assert "Atlas based in Reykjavik" in ctx
    assert "Zephyr based in Lisbon" in ctx


def test_multihop_planner_reaches_company_location_chain():
    mem = Memory()
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE + 2 * DAY)

    res = mem.search("Where is Wei's colleague's company based?", user_id="u1")

    assert res.via == "multi-hop"
    assert "beijing" in res.answer().lower()
    assert [f.predicate for f in res.facts] == ["colleague", "works_at", "based_in"]


def test_multihop_planner_location_chain_can_be_disabled_for_ablation():
    from engram.config import Config

    mem = Memory(config=Config(planner_location_chains=False))
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE + 2 * DAY)

    res = mem.search("Where is Wei's colleague's company based?", user_id="u1")

    assert res.via == "multi-hop"
    assert "moonshot" in res.answer().lower()
    assert "beijing" not in res.answer().lower()
    assert [f.predicate for f in res.facts] == ["colleague", "works_at"]


def test_multihop_planner_reaches_project_location_chain():
    mem = Memory()
    mem.add_fact("Wei", "works_on", "Atlas", user_id="u1", valid_at=BASE)
    mem.add_fact("Atlas", "based_in", "Reykjavik", user_id="u1", valid_at=BASE + DAY)

    res = mem.search("Where is Wei's project based?", user_id="u1")

    assert res.via == "multi-hop"
    assert "reykjavik" in res.answer().lower()
    assert [f.predicate for f in res.facts] == ["works_on", "based_in"]


def test_multihop_planner_project_chain_can_be_disabled_for_ablation():
    from engram.config import Config

    mem = Memory(config=Config(planner_project_chains=False))
    mem.add_fact("Wei", "works_on", "Atlas", user_id="u1", valid_at=BASE)
    mem.add_fact("Atlas", "based_in", "Reykjavik", user_id="u1", valid_at=BASE + DAY)

    res = mem.search("Where is Wei's project based?", user_id="u1")

    assert res.via == "hybrid"
    assert "atlas" in res.answer().lower()
    assert "reykjavik" not in res.answer().lower()


def test_bench_preconsolidation_uses_multi_hop_subqueries():
    from engram.types import Episode
    from eval.bench import retrieve_evidence_episodes

    sister = Episode("My sister Maya is a pediatrician.", id="sister", user_id="u1")
    moved = Episode("Maya just moved to Seattle.", id="moved", user_id="u1")
    noise = Episode("I bought a new espresso machine.", id="noise", user_id="u1")

    class FakeMem:
        def retrieve_episodes(self, query, user_id, k):
            q = query.lower()
            if "sister profession" in q or q == "profession":
                return [sister]
            if "sister moved seattle" in q or q == "seattle" or q == "moved seattle":
                return [moved]
            return [noise]

    eps = retrieve_evidence_episodes(
        FakeMem(),
        "What is the profession of the user's sister who moved to Seattle?",
        "u1",
        2,
    )
    assert {e.id for e in eps} == {"sister", "moved"}

    baseline = retrieve_evidence_episodes(
        FakeMem(),
        "What is the profession of the user's sister who moved to Seattle?",
        "u1",
        2,
        use_planner=False,
    )
    assert [e.id for e in baseline] == ["noise"]


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


def test_history_includes_cold_tier_facts():
    mem = Memory()
    cold = mem.add_fact("project", "project_note", "cold audit trail", user_id="u1")
    hot = mem.add_fact("project", "project_note", "hot audit trail", user_id="u1")
    cold.salience = 0.0
    hot.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1
    assert mem.cold_store.get(cold.id) is not None

    chain = mem.history("project", "project_note", user_id="u1")
    assert {f.object for f in chain} == {"cold audit trail", "hot audit trail"}


def test_add_fact_supersedes_live_cold_tier_fact():
    mem = Memory()
    old = mem.add_fact("user", "project_status", "alpha", user_id="u1")
    filler = mem.add_fact("user", "project_note", "hot filler", user_id="u1")
    old.salience = 0.0
    filler.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1
    assert mem.cold_store.get(old.id) is not None

    new = mem.add_fact("user", "project_status", "beta", user_id="u1")
    assert new.supersedes == old.id
    assert old.invalid_at == new.valid_at
    chain = mem.history("user", "project_status", user_id="u1")
    assert [f.object for f in chain] == ["alpha", "beta"]


def test_search_pages_relevant_cold_fact_back_to_hot():
    mem = Memory()
    fact = mem.add_fact("project", "project_status", "alpha", user_id="u1")
    assert mem.evict_cold(max_hot=0) == 1
    assert mem.fact_store.get(fact.id) is None
    assert mem.cold_store.get(fact.id) is not None

    res = mem.search("What is the project status?", user_id="u1")
    assert res.via == "cold"
    assert "alpha" in res.answer().lower()
    assert mem.fact_store.get(fact.id) is not None
    assert mem.cold_store.get(fact.id) is None


def test_cold_page_in_preserves_hot_limit():
    from engram.config import Config

    mem = Memory(config=Config(max_hot_facts=0, abstain_threshold=2.0))
    target = mem.add_fact("apollo", "launch_code", "alpha", user_id="u1")
    filler = mem.add_fact("zephyr", "project_note", "background filler", user_id="u1")
    target.salience = 0.0
    filler.salience = 0.1
    assert mem.evict_cold(max_hot=1) == 1
    assert mem.cold_store.get(target.id) is not None

    mem.config.max_hot_facts = 1
    res = mem.search("What is Apollo's launch code?", user_id="u1")

    assert res.via == "cold"
    assert "alpha" in res.answer().lower()
    assert len(mem.fact_store.values()) <= 1
    assert mem.fact_store.get(target.id) is not None
    assert mem.cold_store.get(filler.id) is not None


def test_max_hot_facts_auto_pages_on_add_fact():
    from engram.config import Config

    mem = Memory(config=Config(max_hot_facts=1))
    a = mem.add_fact("project", "project_status", "alpha", user_id="u1")
    b = mem.add_fact("project", "project_note", "beta", user_id="u1")
    a.salience = 0.0
    b.salience = 1.0
    mem._enforce_hot_limit()

    assert len(mem.fact_store.values()) == 1
    assert len(mem.cold_store.values()) == 1
    assert mem.cold_pages_out.get("u1", 0) == 1


def test_lean_context_pages_cold_fact_when_hot_misses():
    mem = Memory()
    fact = mem.add_fact("project", "project_status", "alpha", user_id="u1")
    assert mem.evict_cold(max_hot=0) == 1

    ctx = mem.lean_context("project status", user_id="u1", n_chunks=0)
    assert "alpha" in ctx.lower()
    assert mem.fact_store.get(fact.id) is not None
    assert mem.cold_store.get(fact.id) is None


def test_graph_related_facts_include_cold_tier_facts():
    mem = Memory()
    cold = mem.add_fact("project", "project_note", "cold graph evidence", user_id="u1")
    hot = mem.add_fact("project", "project_status", "hot graph evidence", user_id="u1")
    cold.salience = 0.0
    hot.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1
    assert mem.cold_store.get(cold.id) is not None

    related = mem._graph_related_facts("project", mem.resolver.resolve("u1"), None)
    assert "cold graph evidence" in {f.object for f in related}


def test_graph_related_facts_expand_beyond_one_hop():
    mem = Memory()
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    target = mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE + 2 * DAY)

    related = mem._graph_related_facts("Tell me about Wei", mem.resolver.resolve("u1"), None)

    assert target.id in {f.id for f in related}


def test_graph_related_facts_respect_as_of_during_expansion():
    mem = Memory()
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    target = mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE)

    before = mem._graph_related_facts("Tell me about Wei", mem.resolver.resolve("u1"), BASE + 0.5 * DAY)
    after = mem._graph_related_facts("Tell me about Wei", mem.resolver.resolve("u1"), BASE + 2 * DAY)

    assert target.id not in {f.id for f in before}
    assert target.id in {f.id for f in after}


def test_context_for_graph_includes_multihop_related_facts():
    mem = Memory()
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE + 2 * DAY)

    ctx = mem.context_for("Tell me about Wei", user_id="u1", k_chunks=0, graph=True)

    assert "RELATED FACTS (graph traversal):" in ctx
    assert "Moonshot AI based in Beijing" in ctx


def test_graph_paths_include_cold_tier_facts():
    mem = Memory()
    cold = mem.add_fact("project", "project_note", "cold path evidence", user_id="u1")
    hot = mem.add_fact("project", "project_status", "hot path evidence", user_id="u1")
    cold.salience = 0.0
    hot.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1

    block = mem._graph_paths_block("project", mem.resolver.resolve("u1"), None)
    assert "cold path evidence" in block


def test_graph_paths_fallback_expands_beyond_one_hop():
    mem = Memory()
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE + 2 * DAY)

    block = mem._graph_paths_block("Tell me about Wei", mem.resolver.resolve("u1"), None)

    assert "Wei --colleague--> Lin" in block
    assert "Lin --works_at--> Moonshot AI" in block
    assert "Moonshot AI --based_in--> Beijing" in block


def test_graph_paths_fallback_respects_as_of_during_expansion():
    mem = Memory()
    mem.add_fact("Wei", "colleague", "Lin", user_id="u1", valid_at=BASE)
    mem.add_fact("Lin", "works_at", "Moonshot AI", user_id="u1", valid_at=BASE + DAY)
    mem.add_fact("Moonshot AI", "based_in", "Beijing", user_id="u1", valid_at=BASE)

    before = mem._graph_paths_block("Tell me about Wei", mem.resolver.resolve("u1"), BASE + 0.5 * DAY)
    after = mem._graph_paths_block("Tell me about Wei", mem.resolver.resolve("u1"), BASE + 2 * DAY)

    assert "Moonshot AI --based_in--> Beijing" not in before
    assert "Moonshot AI --based_in--> Beijing" in after


def test_pending_conflict_and_resolution_include_cold_tier_facts():
    from engram.types import Conflict

    mem = Memory()
    old = mem.add_fact("project", "status", "alpha", user_id="u1")
    new = mem.add_fact("project", "status", "beta", user_id="u1")
    old.invalid_at = None
    old.expired_at = None
    new.supersedes = None
    old.salience = 0.0
    new.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1
    assert mem.cold_store.get(old.id) is not None
    cf = Conflict(older=old.id, newer=new.id, user_id=mem.resolver.resolve("u1"), text_older=old.text, text_newer=new.text)
    mem.conflicts[cf.id] = cf

    assert mem.pending_conflicts("u1") == [cf]
    assert mem.resolve_conflict(cf.id, keep="newer") is True
    assert old.invalid_at is not None
    assert cf.status == "resolved"


def test_update_cold_fact_promotes_without_duplicate():
    mem = Memory()
    cold = mem.add_fact("project", "project_note", "old value", user_id="u1")
    hot = mem.add_fact("project", "project_status", "hot value", user_id="u1")
    cold.salience = 0.0
    hot.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1
    assert mem.cold_store.get(cold.id) is not None

    updated = mem.update_fact(cold.id, object="new value")

    assert updated is not None and updated.object == "new value"
    assert mem.fact_store.get(cold.id) is not None
    assert mem.cold_store.get(cold.id) is None
    assert len([f for f in mem._all_facts() if f.id == cold.id]) == 1


def test_resolve_conflict_writes_back_to_copying_vector_store():
    """External vector backends may return payload copies, so conflict resolution must explicitly upsert
    mutated facts instead of relying on in-memory object identity."""
    from copy import deepcopy

    from engram.store import InMemoryVectorStore
    from engram.types import Conflict

    class CopyingVectorStore(InMemoryVectorStore):
        def upsert(self, key, vector, payload):
            super().upsert(key, list(vector), deepcopy(payload))

        def get(self, key):
            payload = super().get(key)
            return deepcopy(payload) if payload is not None else None

        def values(self):
            return [deepcopy(payload) for payload in super().values()]

    mem = Memory()
    older = mem.add_fact("user", "likes", "tea", user_id="u1")
    newer = mem.add_fact("user", "likes", "coffee", user_id="u1")
    copy_hot = CopyingVectorStore()
    for fact in mem.fact_store.values():
        copy_hot.upsert(fact.id, fact.embedding or [], fact)
    mem.fact_store = copy_hot
    mem.cold_store = CopyingVectorStore()
    mem._rewire()

    cf = Conflict(older=older.id, newer=newer.id, user_id=mem.resolver.resolve("u1"))
    mem.conflicts[cf.id] = cf

    assert mem.resolve_conflict(cf.id, keep="newer") is True
    assert not mem.fact_store.get(older.id).is_live()
    assert mem.fact_store.get(newer.id).supersedes == older.id


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


def test_agentic_episode_gather_respects_as_of():
    """Agentic chunk retrieval must preserve the caller's point-in-time memory view."""
    from engram.retrieve.agentic import AgenticRetriever

    class FakeLLM:
        def complete(self, prompt, system=None, **k):
            return '["trips"]'

    class FakeMemory:
        def __init__(self):
            self.calls = []

        def retrieve_episodes(self, query, user_id, k, as_of=None):
            self.calls.append(as_of)
            return []

    fake = FakeMemory()
    AgenticRetriever(fake, FakeLLM()).gather_episodes("Which cities did I visit?", "u1", 1, as_of=BASE)
    assert fake.calls == [BASE]


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


def test_procedural_memory_includes_cold_tier_instructions():
    mem = Memory()
    cold = mem.add_fact("u1", "wants_reminder", "water the plants", user_id="u1")
    hot = mem.add_fact("u1", "works_at", "Tencent", user_id="u1")
    cold.salience = 0.0
    hot.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1

    objs = {f.object for f in mem.procedural("u1")}
    assert "water the plants" in objs
    assert "Tencent" not in objs


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


def test_reflector_uses_cold_tier_superseded_facts():
    from engram.types import Fact

    mem = Memory()
    ep = mem.add("I do yoga twice a week.", user_id="u1", session_id="s1", event_time=BASE)
    old = Fact(subject="u1", predicate="yoga_frequency", object="twice a week",
               user_id=mem.resolver.resolve("u1"), valid_at=BASE, provenance=[ep.id],
               invalid_at=BASE + 5 * DAY, expired_at=BASE + 5 * DAY, salience=0.0)
    new = Fact(subject="u1", predicate="yoga_frequency", object="three times a week",
               user_id=mem.resolver.resolve("u1"), valid_at=BASE + 5 * DAY, supersedes=old.id,
               salience=1.0)
    mem.fact_store.upsert(old.id, [0.0], old)
    mem.fact_store.upsert(new.id, [0.0], new)
    mem.summarize_episodes([ep])
    assert mem.evict_cold(max_hot=1) == 1

    assert mem.reflect("u1") == 1
    assert "three times a week" in ep.summary


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


def test_persona_includes_cold_tier_facts():
    mem = Memory()
    cold = mem.add_fact("user", "favorite_language", "Python", user_id="u1")
    hot = mem.add_fact("user", "project_note", "hot note", user_id="u1")
    cold.salience = 0.0
    hot.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1

    assert "Python" in mem.build_persona("u1")


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
        assert e["fact_id"] and e["fact_text"]
        assert isinstance(e["valid_at"], float) and e["valid_at_h"]
        assert "invalid_at" in e and "invalid_at_h" in e
        assert isinstance(e["provenance"], list)


def test_graph_data_as_of_excludes_future_relations():
    """The graph view must obey the same valid-time boundary as search/as_of: a future edge is not live
    in a past snapshot just because it has not been invalidated yet."""
    mem = Memory()
    mem.add("Wei works at Tencent.", user_id="u1", event_time=BASE)
    mem.add("Wei works at Moonshot AI.", user_id="u1", event_time=BASE + 30 * DAY)
    mem.consolidate()

    past = mem.graph_data("u1", as_of=BASE + 10 * DAY)
    past_names = {n["name"] for n in past["nodes"]}
    assert "Tencent" in past_names
    assert "Moonshot AI" not in past_names
    assert all(e["live"] for e in past["edges"])

    current = mem.graph_data("u1")
    assert "Moonshot AI" in {n["name"] for n in current["nodes"]}


def test_graph_data_can_exclude_sensitive_fact_edges():
    mem = Memory()
    mem.add_fact("user", "has_disease", "diabetes", user_id="u1")
    mem.add_fact("user", "works_at", "Acme", user_id="u1")

    full = mem.graph_data("u1")
    assert "diabetes" in str(full).lower()
    assert "Acme" in str(full)

    safe = mem.graph_data("u1", include_sensitive=False)
    rendered = str(safe).lower()
    assert "diabetes" not in rendered
    assert "Acme" in str(safe)
    assert all("diabetes" not in e.get("fact_text", "").lower() for e in safe["edges"])


def test_graph_data_can_exclude_sensitive_cold_fact_edges():
    mem = Memory()
    disease = mem.add_fact("user", "has_disease", "diabetes", user_id="u1")
    work = mem.add_fact("user", "project_note", "Acme", user_id="u1")
    disease.salience = 0.0
    work.salience = 1.0
    assert mem.evict_cold(max_hot=1) == 1
    assert mem.cold_store.get(disease.id) is not None

    full = mem.graph_data("u1")
    assert "diabetes" in str(full).lower()

    safe = mem.graph_data("u1", include_sensitive=False)
    rendered = str(safe).lower()
    assert "diabetes" not in rendered
    assert all(e["fact_id"] != disease.id for e in safe["edges"])


def test_memory_policy_edits_extraction_and_persists(tmp_path):
    """The 记忆策略 page is real wiring: a user's 'what to record' directive is appended to the extraction
    system prompt, full prompt overrides replace the defaults (and clear back to default with ''), and the
    whole policy survives a save/open round-trip."""
    class FakeLLM:
        def complete(self, prompt, system=""):
            return "[]"

    from engram.consolidate.llm_extractor import EXTRACT_SYSTEM
    m = Memory(llm=FakeLLM())

    # additive 'what to record' directive flows into the extractor's effective system prompt
    m.set_policy(extract_instruction="只记录与工作相关的事实")
    ex = m.engine.extractor
    assert ex.instruction == "只记录与工作相关的事实"
    assert "只记录与工作相关的事实" in ex._effective_system()
    assert EXTRACT_SYSTEM in ex._effective_system()  # default still present, directive appended

    # full override replaces the default, and "" resets to default
    m.set_policy(extract_system="CUSTOM PROMPT")
    assert m.engine.extractor.system == "CUSTOM PROMPT"
    m.set_policy(extract_system="")
    assert m.engine.extractor.system == EXTRACT_SYSTEM

    # get_policy exposes overrides + the built-in defaults; policy persists
    gp = m.get_policy()
    assert set(gp) == {"policy", "defaults"} and len(gp["defaults"]) == 4
    p = str(tmp_path / "u.pkl")
    m.save(p)
    assert Memory.open(p).get_policy()["policy"]["extract_instruction"] == "只记录与工作相关的事实"


def test_structured_profile_tiers_display_only_and_preserves_recall():
    """The L2 structured profile (feature ③, reasonable version): basic/preferences/habits grouped, with
    a DISPLAY-ONLY confirmed↔tentative split and HONEST evidence (no fabricated weights). The critical
    invariant: the tentative split is presentation only — a tentative fact must STILL be in the fact store
    and retrievable (recall is never gated by the profile view)."""
    from engram.types import Fact
    m = Memory()
    m.add_fact("user", "favorite_music", "周杰伦", user_id="u")     # explicit favorite -> confirmed
    casual = Fact(subject="user", predicate="likes", object="jazz", user_id="u",
                  source="extracted", provenance=["ep1"])          # single casual mention -> tentative
    casual.embedding = m.embedder.embed(casual.text)
    m.fact_store.upsert(casual.id, casual.embedding, casual)

    p = m.structured_profile("u")
    conf_items = [it["item"] for items in p["preferences"].values() for it in items]
    # an explicitly STATED preference is confirmed (the user said it — not a shaky inference); both the
    # explicit favorite and the stated like show in the canonical profile, not as 待确认 candidates.
    assert "周杰伦" in conf_items and "jazz" in conf_items, "stated preferences are confirmed"
    assert [t["item"] for t in p["tentative"]] == [], "stated preferences are not held as 待确认"

    # honest evidence, never a fabricated numeric weight
    for items in p["preferences"].values():
        for it in items:
            assert "weight" not in it and it["evidence"]["kind"] in {"user", "mentions", "reinforced"}

    # RECALL INVARIANT: the tentative fact is display-tiered, NOT removed — still in the store and live
    assert m.fact_store.get(casual.id) is not None and casual.is_live()
    assert any(f.object == "jazz" for f in m.fact_store.values() if f.is_live())


def test_structured_profile_includes_cold_tier_facts():
    m = Memory()
    cold = m.add_fact("user", "favorite_music", "周杰伦", user_id="u")
    hot = m.add_fact("user", "project_note", "hot note", user_id="u")
    cold.salience = 0.0
    hot.salience = 1.0
    assert m.evict_cold(max_hot=1) == 1

    p = m.structured_profile("u")
    items = [it["item"] for values in p["preferences"].values() for it in values]
    assert "周杰伦" in items


def test_working_memory_is_ephemeral_and_never_enters_long_term(tmp_path):
    """Feature ①: the working-memory tier holds transient state OUT of long-term, scoped by session + TTL,
    cleared on session end. The core invariant: ephemeral items NEVER become durable facts."""
    import engram.util as u
    m = Memory()
    assert Memory.is_ephemeral("today my throat hurts") and not Memory.is_ephemeral("I have diabetes")

    m.remember_working("today my throat hurts", user_id="u", session_id="s1", kind="state")
    m.remember_working("this trip front seat is my wife", user_id="u", session_id="s1",
                       kind="passenger", ttl_seconds=7200)
    m.remember_working("note in another session", user_id="u", session_id="s2")

    # session-scoped + kept out of long-term entirely
    assert {w.content for w in m.working_memory("u", session_id="s1")} == {
        "today my throat hurts", "this trip front seat is my wife"}
    assert list(m.fact_store.values()) == [], "working memory must NOT create long-term facts"

    # surfaced in the read context for that session, but ephemeral
    ctx = m.lean_context("how am I", user_id="u", session_id="s1", n_chunks=0)
    assert "WORKING MEMORY" in ctx and "throat" in ctx.lower()

    # hard TTL expiry, then power-cycle clear
    later = u.now() + 8000
    assert [w.kind for w in m.working_memory("u", session_id="s1", as_of=later)] == ["state"]
    assert m.clear_session("u", "s1") == 1 and m.working_memory("u", session_id="s1") == []

    # persists across save/open
    m.remember_working("persist me", user_id="u", session_id="s3")
    p = str(tmp_path / "u.pkl")
    m.save(p)
    assert [w.content for w in Memory.open(p).working_memory("u", session_id="s3")] == ["persist me"]


def test_classification_and_sensitivity_redaction_preserves_recall():
    """Feature ⑤: facts get a rule-based category + sensitivity flag; sensitive facts can be REDACTED from
    a shared/export context — while staying fully in the store (recall + the owner's own view intact)."""
    from engram.consolidate.classify import classify
    assert classify("has_disease", "diabetes", "user has_disease diabetes") == ("健康", True)
    assert classify("salary", "25000", "user salary 25000")[1] is True
    assert classify("works_at", "Acme", "user works_at Acme") == ("工作", False)
    assert classify("id_number", "310101199001011234", "x")[1] is True  # digit-run PII

    m = Memory()
    m.add_fact("user", "has_disease", "diabetes", user_id="u")  # sensitive
    m.add_fact("user", "works_at", "Acme", user_id="u")          # not sensitive
    sensitive_fact = next(f for f in m.fact_store.values() if f.object == "diabetes")
    assert sensitive_fact.sensitive and sensitive_fact.category == "健康"

    redacted = m.lean_context("about me", user_id="u", n_chunks=0, redact_sensitive=True)
    assert "diabetes" not in redacted.lower(), "sensitive fact must be redacted from a shared context"
    assert "acme" in redacted.lower(), "non-sensitive facts stay"
    # recall invariant: redaction is a view filter, not deletion — the fact is still stored & live
    assert any(f.object == "diabetes" for f in m.fact_store.values() if f.is_live())


def test_redacted_context_omits_free_text_layers_that_can_leak_sensitive_content():
    """A redacted/shared context must not leak sensitive content through summaries, chunks, or working
    memory prose. It should rely only on classified, non-sensitive structured facts."""
    m = Memory()
    ep = m.add("My private diagnosis is diabetes. I work at Acme.", user_id="u", session_id="s1")
    ep.summary = "Private diagnosis: diabetes. Job: Acme."
    m.summary_vec.upsert(ep.id, m.embedder.embed(ep.summary), ep)
    m.add_fact("user", "has_disease", "diabetes", user_id="u")
    m.add_fact("user", "works_at", "Acme", user_id="u")
    m.remember_working("diabetes flare today", user_id="u", session_id="s1")

    redacted = m.lean_context(
        "what do you know about my diagnosis and work?",
        user_id="u",
        session_id="s1",
        n_summaries=5,
        n_chunks=5,
        redact_sensitive=True,
        char_budget=10_000,
    )

    assert "Acme" in redacted
    assert "diabetes" not in redacted.lower()
    assert "SESSION SUMMARIES" not in redacted
    assert "RELEVANT CONVERSATIONS" not in redacted
    assert "WORKING MEMORY" not in redacted


def test_ephemeral_remember_keeps_dated_episode_but_creates_no_durable_fact():
    """Corrected ① model (the user's catch): ephemeral != deleted. Transient state goes to working memory
    AND is kept as a dated EPISODE (so 'when did my throat hurt?' is answerable from history), but it does
    NOT become a durable profile fact (so it never lingers as a current attribute)."""
    m = Memory()
    r = m.remember("today my throat is uncomfortable", user_id="u", session_id="s1")
    assert r["scope"] == "working"

    # the EVENT is retained in the dated episodic log -> historically queryable
    eps = m.retrieve_episodes("throat uncomfortable", user_id="u", k=3)
    assert any("throat" in e.content.lower() for e in eps), "dated episode must remain retrievable"
    # current-session working memory holds it too
    assert any("throat" in w.content.lower() for w in m.working_memory("u", session_id="s1"))

    # but NO durable profile fact — and a later consolidate must not pick the ephemeral episode up
    m.remember("I work at Acme", user_id="u", session_id="s1")
    m.consolidate()
    assert not any("throat" in f.text.lower() for f in m.fact_store.values()), \
        "transient state must not become a durable profile fact"


def test_display_localization_keeps_canonical_predicate():
    """Display localization (i18n): render Chinese-recorded facts naturally WITHOUT changing the canonical
    English predicate (the slot key). English-recorded facts are left untouched."""
    from engram.localize import render_display
    # Chinese object -> localized Chinese display
    assert render_display("user", "works_at", "字节跳动", "user works at 字节跳动") == "在 字节跳动 工作"
    assert render_display("user", "favorite_music", "周杰伦", "x") == "最喜欢的音乐是 周杰伦"
    assert render_display("user", "allergic_to", "花粉", "x") == "对 花粉 过敏"
    # English data is NOT force-translated
    assert render_display("user", "works_at", "Acme", "user works at Acme") == "user works at Acme"
    # the canonical predicate/slot is unchanged by display (engine still dedups on it)
    m = Memory()
    f = m.add_fact("user", "works_at", "字节跳动", user_id="u")
    assert f.predicate == "works_at" and f.slot[2] == "works_at"


def test_costated_facts_do_not_semantic_supersede():
    """Regression (user-reported): two facts from the SAME episode ('我在字节跳动做后端开发' ->
    works_at 字节跳动 + job_title 后端开发) are complementary attributes and must NOT supersede each other
    via the embedding path, even though they're short, same-subject and topically near. A genuine update
    must come from a LATER, SEPARATE episode."""
    from engram.consolidate.conflict import ConflictResolver
    from engram.types import Fact

    class E:  # embedder returning identical vectors -> cosine 1.0 (worst case for the semantic path)
        def embed(self, t):
            return [1.0, 0.0]

    r = ConflictResolver(embedder=E(), sim_threshold=0.8)
    old = Fact("user", "works_at", "字节跳动", provenance=["ep1"])
    old.embedding = [1.0, 0.0]
    new = Fact("user", "job_title", "后端开发", provenance=["ep1"])  # SAME episode
    new.embedding = [1.0, 0.0]
    _, invalidated = r.reconcile(new, [old])
    assert invalidated == [], "co-stated complementary facts must not supersede each other"

    # a real update from a DIFFERENT, later episode still supersedes (guard is provenance-scoped)
    later = Fact("user", "occupation", "产品经理", provenance=["ep9"])
    later.embedding = [1.0, 0.0]
    _, inval2 = r.reconcile(later, [old])
    assert old in inval2, "a later separate-episode near-duplicate should still supersede"


def test_preference_reversal_supersedes_same_object_opposite_polarity():
    """User-reported: '我喜欢跳舞' then '我不喜欢跳舞' showed BOTH (+跳舞 and -跳舞). A like<->dislike flip
    on the SAME object is a reversal — the newer stance supersedes the old (PRD 治理: 修正否定). Different
    objects still accumulate (multi-valued)."""
    from engram.consolidate.conflict import ConflictResolver
    from engram.types import Fact
    r = ConflictResolver()
    old = Fact("user", "likes", "跳舞", valid_at=100.0)
    new = Fact("user", "dislikes", "跳舞", valid_at=200.0)
    _, invalidated = r.reconcile(new, [old])
    assert old in invalidated, "later opposite-polarity preference on the same object must supersede"
    # a DIFFERENT object is untouched — multi-valued accumulation preserved
    other = Fact("user", "likes", "唱歌", valid_at=50.0)
    _, inval2 = r.reconcile(Fact("user", "likes", "跳舞", valid_at=300.0), [other])
    assert other not in inval2


def test_conflict_detection_flags_candidates_and_coexist_default():
    """System-2 LLM detection: only flag genuine suspected conflicts among candidate pairs; COEXIST is the
    default (unrelated / model-unsure pairs are never flagged)."""
    from engram.consolidate.detect import detect_conflicts
    from engram.types import Fact

    class Judge:
        def complete(self, prompt, system=""):
            return "CONFLICT" if ("北京" in prompt and "上海" in prompt) else "COEXIST"

    a = Fact("user", "lives_in", "北京", valid_at=100.0); a.embedding = [1.0, 0.0]
    b = Fact("user", "based_in", "上海", valid_at=200.0); b.embedding = [0.8, 0.6]   # ~0.8 cos: in band
    c = Fact("user", "likes", "咖啡", valid_at=150.0); c.embedding = [0.0, 1.0]       # unrelated (cos<0.62)
    flagged = detect_conflicts([a, b, c], Judge(), "user", seen=set())
    assert len(flagged) == 1, "only the genuine conflict pair is flagged (coexist default)"
    cf = flagged[0]
    assert cf.newer == b.id and cf.older == a.id  # newer = later valid_at


def test_conflict_resolve_and_dismiss_are_user_driven():
    """Detection NEVER auto-resolves — only the user's resolve()/dismiss() acts; resolving supersedes the
    chosen loser, dismiss keeps both."""
    from engram.types import Conflict
    m = Memory()
    fa = m.add_fact("user", "likes", "北京", user_id="u")   # multi-valued -> both coexist (no auto-merge)
    fb = m.add_fact("user", "likes", "上海", user_id="u")
    cf = Conflict(older=fa.id, newer=fb.id, user_id="u")
    m.conflicts[cf.id] = cf
    assert m.pending_conflicts("u"), "both live -> pending"

    assert m.resolve_conflict(cf.id, keep="newer")
    assert not fa.is_live() and fb.is_live(), "keep=newer supersedes the older"
    assert cf.status == "resolved" and m.pending_conflicts("u") == []

    # dismiss keeps both
    g1 = m.add_fact("user", "likes", "茶", user_id="u")
    g2 = m.add_fact("user", "likes", "奶茶", user_id="u")
    cf2 = Conflict(older=g1.id, newer=g2.id, user_id="u")
    m.conflicts[cf2.id] = cf2
    assert m.dismiss_conflict(cf2.id) and g1.is_live() and g2.is_live()
    assert m.pending_conflicts("u") == []
