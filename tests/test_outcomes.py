"""Session outcomes — the memory unit that matches how the owner actually works.

The per-episode extractor looks for biographical triples, which is right for LongMemEval and wrong for a
technical working session: on a real 115-turn transcript it produced
`user requires_no_commit_push_publish = true` (a one-off task constraint promoted to a durable
attribute), and 3353 turns across ten sessions yielded five facts. Asked for decisions and lessons
instead, the same model returned material equivalent to what the owner writes into markdown by hand."""
from __future__ import annotations

from engram.consolidate.outcomes import (
    OUTCOME_CATEGORY,
    OUTCOME_PREDICATES,
    OUTCOME_SYSTEM,
    _windowed,
    extract_outcomes,
    split_outcome_text,
)
from engram.memory import Memory
from engram.service import MemoryService
from engram.types import Episode, Fact


class StubLLM:
    """Records the prompt so the tests can assert how the transcript is presented."""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompt = ""
        self.system = ""

    def complete(self, prompt: str, system: str = "", **kwargs) -> str:
        self.prompt, self.system = prompt, system
        return self.reply


def _episodes(n: int = 3) -> list[Episode]:
    return [Episode(content=f"turn {i} with enough words to survive filtering", user_id="u",
                    session_id="s1", speaker="user" if i % 2 else "assistant",
                    event_time=1_700_000_000.0 + i)
            for i in range(n)]


def test_outcomes_become_ordinary_facts_with_kind_predicates():
    """No new type and no new store: outcomes ride the Fact table so they inherit bi-temporal validity,
    supersession and provenance for free."""
    llm = StubLLM('[{"kind":"lesson","statement":"Running --pair overwrites the single global config",'
                  '"why":"took four agents offline"}]')
    facts = extract_outcomes(llm, _episodes(), "u", session_id="s1")
    assert len(facts) == 1
    f = facts[0]
    assert f.predicate == "lesson" and f.predicate in OUTCOME_PREDICATES
    assert f.object.startswith("Running --pair")
    assert "took four agents offline" in f.text  # the reasoning is embedded, so cause is searchable
    assert f.display == f.object
    # attributed to the whole session: a conclusion is supported by the arc, not by one turn
    assert len(f.provenance) == 3
    assert f.valid_at == _episodes()[-1].event_time


def test_unknown_kinds_and_degenerate_statements_are_rejected():
    llm = StubLLM('['
                  '{"kind":"gossip","statement":"something long enough to pass length checks"},'
                  '{"kind":"lesson","statement":"too short"},'
                  '{"kind":"finding","statement":"' + "x" * 500 + '"},'
                  '{"kind":"finding","statement":"A real finding with adequate length here"}]')
    facts = extract_outcomes(llm, _episodes(), "u")
    assert [f.object for f in facts] == ["A real finding with adequate length here"]


def test_duplicate_statements_collapse():
    llm = StubLLM('[{"kind":"decision","statement":"We chose rsync over docker compose"},'
                  '{"kind":"decision","statement":"We chose rsync over docker compose"}]')
    assert len(extract_outcomes(llm, _episodes(), "u")) == 1


def test_transcript_is_fenced_as_data_not_conversation():
    """A body starting with "[assistant] ..." was read as a conversation to continue: on a real
    1164-turn session the model wrote the next turn instead of extracting, and yielded nothing."""
    llm = StubLLM("[]")
    extract_outcomes(llm, _episodes(), "u")
    assert "<session>" in llm.prompt and "</session>" in llm.prompt
    assert not llm.prompt.lstrip().startswith("[")


def test_long_sessions_keep_both_ends():
    """Plain truncation showed the model the first 7% of a 335k-char session — the setup, before
    anything was decided. Conclusions live at the end."""
    turns = [f"turn {i} " + "x" * 200 for i in range(300)]
    out = _windowed(turns, 4000)
    assert len(out) <= 4100
    assert "turn 0 " in out and "turn 299 " in out and "省略" in out


def test_a_session_with_nothing_durable_yields_nothing():
    assert extract_outcomes(StubLLM("[]"), _episodes(), "u") == []


def test_model_outage_never_loses_the_session():
    class Broken:
        def complete(self, *a, **k):
            raise RuntimeError("provider down")

    assert extract_outcomes(Broken(), _episodes(), "u") == []


def test_truncated_model_output_salvages_complete_objects():
    """A reply cut off by max_tokens used to discard every extracted item — which is exactly what long
    sessions produce, since the model fills its budget."""
    from engram.consolidate.llm_extractor import parse_json_facts

    truncated = ('[{"kind":"finding","statement":"first complete one","why":"a"},'
                 '{"kind":"lesson","statement":"second complete one","why":"b"},'
                 '{"kind":"decis')
    got = parse_json_facts(truncated)
    assert [g["statement"] for g in got] == ["first complete one", "second complete one"]


def test_split_outcome_text_is_the_exact_inverse_of_the_format():
    """The formatter and this parser sit ten lines apart on purpose: a separator change must break here."""
    llm = StubLLM('[{"kind":"finding","statement":"client.ts declares its own copy of the API types",'
                  '"why":"a contract change failed at runtime, not compile time"}]')
    f = extract_outcomes(llm, _episodes(), "u", session_id="s1")[0]
    assert split_outcome_text(f.text) == (
        "client.ts declares its own copy of the API types",
        "a contract change failed at runtime, not compile time",
    )
    # A statement recorded without evidence round-trips to an empty why, not to a stray paren.
    assert split_outcome_text("just a statement") == ("just a statement", "")


# --- service level: the outcome layer as the console and MCP clients actually see it ---

def _svc(tmp_path, llm=None):
    """Offline service (llm_name="" => self.llm is None), optionally given a stub LLM afterwards."""
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    if llm is not None:
        svc.llm = llm
    return svc


def _session(svc, user="u", session_id="s1"):
    svc.remember(user, "我们把部署方式从 docker compose 换成了 rsync", session_id=session_id)
    svc.remember(user, "重新配对会覆盖唯一的全局配置文件", session_id=session_id)


def test_close_session_produces_nothing_without_an_llm(tmp_path):
    """The zero-setup guard: session_outcomes defaults to True, but no key means no LLM, hence no call
    and no outcome facts — pytest and quickstart stay offline and deterministic."""
    svc = _svc(tmp_path)
    assert svc.config.session_outcomes is True
    assert svc.llm is None
    _session(svc)
    assert svc.close_session("u", "s1")["outcomes"] == 0


def test_env_kill_switch_turns_outcomes_off(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_SESSION_OUTCOMES", "0")
    assert _svc(tmp_path).config.session_outcomes is False


def test_env_kill_switch_outranks_a_client_opt_in(tmp_path, monkeypatch):
    """The switch is the operator's cost ceiling, so it cannot be only a default: /v1/sessions/close
    takes an `outcomes` field and connectors/watch.py posts `outcomes: True` on every session it
    imports — the one caller that runs unattended. A default-only switch would leave that path billing."""
    monkeypatch.setenv("ENGRAM_SESSION_OUTCOMES", "0")
    llm = StubLLM('[{"kind":"decision","statement":"部署方式从 docker compose 换成 rsync",'
                  '"why":"compose 在共享机上会重建别人的容器"}]')
    svc = _svc(tmp_path, llm)
    _session(svc)
    assert svc.close_session("u", "s1", outcomes=True)["outcomes"] == 0
    # Not just "no fact written": the distillation call itself never happened. The outcome call is the
    # last LLM call in a close, so a stub that never saw OUTCOME_SYSTEM was never asked to distil.
    assert llm.system != OUTCOME_SYSTEM
    assert svc.memories("u", kind="outcomes")["facts"] == []


def test_reclosing_an_unchanged_session_adds_no_duplicates(tmp_path):
    """connectors/watch.py re-sends any transcript that grew and closes it again; extract_outcomes only
    dedupes within one call, so without a guard every re-close doubles the conclusions."""
    llm = StubLLM('[{"kind":"decision","statement":"部署方式从 docker compose 换成 rsync",'
                  '"why":"compose 在共享机上会重建别人的容器"}]')
    svc = _svc(tmp_path, llm)
    _session(svc)
    assert svc.close_session("u", "s1")["outcomes"] == 1
    assert svc.close_session("u", "s1")["outcomes"] == 0
    outcomes = svc.memories("u", kind="outcomes")["facts"]
    assert len(outcomes) == 1


def test_kind_partitions_the_fact_set_exactly(tmp_path):
    llm = StubLLM('[{"kind":"lesson","statement":"重新配对会覆盖唯一的全局配置文件",'
                  '"why":"四个 agent 掉线"}]')
    svc = _svc(tmp_path, llm)
    _session(svc)
    svc.add_fact("u", "user", "occupation", "engineer")
    svc.close_session("u", "s1")

    everything = svc.memories("u")
    outcomes = svc.memories("u", kind="outcomes")
    attributes = svc.memories("u", kind="attributes")
    ids = lambda page: {f["id"] for f in page["facts"]}  # noqa: E731
    assert ids(outcomes) and ids(attributes)
    assert ids(outcomes) | ids(attributes) == ids(everything)
    assert not (ids(outcomes) & ids(attributes))
    assert all(f["predicate"] in OUTCOME_PREDICATES for f in outcomes["facts"])
    # counts are computed over the unfiltered set, so they agree across all three views
    assert everything["counts"]["facts_outcomes"] == 1
    assert attributes["counts"]["facts_outcomes"] == 1


def test_unknown_kind_is_ignored_not_rejected(tmp_path):
    """Same contract as `status`: a stale client sending a value we do not know still gets its memory."""
    svc = _svc(tmp_path)
    svc.add_fact("u", "user", "occupation", "engineer")
    assert len(svc.memories("u", kind="banana")["facts"]) == 1
    assert len(svc.memories("u", kind="")["facts"]) == 1


def test_outcome_facts_carry_their_evidence_split_out(tmp_path):
    llm = StubLLM('[{"kind":"finding","statement":"共享机上不要执行 docker build","why":"会重建别人的容器"}]')
    svc = _svc(tmp_path, llm)
    _session(svc)
    svc.add_fact("u", "user", "occupation", "engineer")
    svc.close_session("u", "s1")
    row = svc.memories("u", kind="outcomes")["facts"][0]
    assert row["why"] == "会重建别人的容器"
    assert svc.memories("u", kind="attributes")["facts"][0]["why"] == ""


def test_outcomes_never_reach_the_graph(tmp_path):
    """A conclusion's object is a sentence and its subject is a session id, so an edge would mint one
    junk entity per statement plus one per session — and then the audit would report them as orphans."""
    llm = StubLLM('[{"kind":"lesson","statement":"重新配对会覆盖唯一的全局配置文件","why":"四个 agent 掉线"}]')
    svc = _svc(tmp_path, llm)
    _session(svc)
    svc.close_session("u", "s1")
    names = {n["name"] for n in svc.graph("u", include_sensitive=True)["nodes"]}
    assert "s1" not in names
    assert not any("重新配对会覆盖" in n for n in names)


def test_audit_reports_one_slot_overflow_and_leaves_outcomes_alone(tmp_path):
    """The owner's real store held 84 live `occupation` facts. Every row passes the per-fact rules; the
    defect is only visible as a slot. Reported once, not 84 times."""
    llm = StubLLM('[{"kind":"lesson","statement":"配置文件路径 ~/.cumora/computer.json 没有覆盖机制",'
                  '"why":"跑 --pair 覆盖了线上那份"}]')
    svc = _svc(tmp_path, llm)
    _session(svc)
    svc.close_session("u", "s1")
    # Written straight into the store, not through add_fact: conflict resolution would supersede each
    # previous value on this single-valued slot, which is exactly the collapse that DIDN'T happen in the
    # field — the per-turn extractor left all 84 live, and that is the state the check has to catch.
    mem = svc.get("u")
    for i in range(10):
        junk = Fact(subject="user", predicate="occupation", object=f"borrow from big tech {i}",
                    user_id="u")
        junk.embedding = mem.embedder.embed(junk.text)
        mem.fact_store.upsert(junk.id, junk.embedding, junk)

    audit = svc.audit("u", limit=200)
    overflow = [r for r in audit["findings"] if r["kind"] == "slot_overflow"]
    assert len(overflow) == 1 and audit["by_kind"]["slot_overflow"] == 1
    assert overflow[0]["count"] == 10 and overflow[0]["predicate"] == "occupation"
    assert len(overflow[0]["samples"]) == 5
    assert "fact_id" not in overflow[0]  # a group finding has no single row to edit or delete
    # the 10 rows are folded into the group, and the session conclusion is not junk to be fixed
    outcome_ids = {f["id"] for f in svc.memories("u", kind="outcomes")["facts"]}
    assert not any(r.get("fact_id") in outcome_ids for r in audit["findings"])
    assert not [r for r in audit["findings"] if r.get("predicate") == "occupation"
                and r["kind"] != "slot_overflow"]


def test_editing_an_outcome_keeps_its_shape_and_refreshes_display(tmp_path):
    """The generic rebuild would write "s1 lesson ..." into `text` — embedding the session id and losing
    the 依据 clause recall matches on — and leave `display` showing the pre-edit wording forever."""
    llm = StubLLM('[{"kind":"lesson","statement":"重新配对会覆盖唯一的全局配置文件","why":"四个 agent 掉线"}]')
    svc = _svc(tmp_path, llm)
    _session(svc)
    svc.close_session("u", "s1")
    fact_id = svc.memories("u", kind="outcomes")["facts"][0]["id"]

    svc.update_fact("u", fact_id, object="重新配对会覆盖 ~/.cumora/computer.json")
    row = [f for f in svc.memories("u", kind="outcomes")["facts"] if f["id"] == fact_id][0]
    assert row["text"] == "重新配对会覆盖 ~/.cumora/computer.json （依据：四个 agent 掉线）"
    assert "s1" not in row["text"]
    assert row["why"] == "四个 agent 掉线"
    assert row["display"] == "重新配对会覆盖 ~/.cumora/computer.json"


def test_editing_an_attribute_does_not_collapse_its_display_to_the_bare_object(tmp_path):
    """The sibling of the test above, and the regression it invites: `display` is reassignable for an
    outcome only because outcomes.py writes `display=statement`. The LLM extractor writes a whole
    sentence there, so reusing `display = object` for attributes would render an edited fact as
    "Moonshot AI" instead of "Wei works at Moonshot AI" — fragments again, on the other side of `kind`."""
    svc = _svc(tmp_path)
    mem = svc.get("u")
    f = Fact(user_id="u", subject="Wei", predicate="works_at", object="Tencent",
             text="Wei works at Tencent", display="Wei works at Tencent")
    f.embedding = mem.embedder.embed(f.text)
    mem.fact_store.upsert(f.id, f.embedding, f)

    svc.update_fact("u", f.id, object="Moonshot AI")
    row = [r for r in svc.memories("u", kind="attributes")["facts"] if r["id"] == f.id][0]
    assert row["text"] == "Wei works at Moonshot AI"
    assert row["display"] == "Wei works at Moonshot AI"  # not the bare "Moonshot AI"
    assert row["why"] == ""


def _stored_outcome(mem, predicate="finding", statement="分页游标在第二页丢失排序键", why="实测第二页重复"):
    f = Fact(user_id="u", subject="s-1", predicate=predicate, object=statement,
             text=f"{statement} （依据：{why}）", display=statement, category=OUTCOME_CATEGORY)
    f.embedding = mem.embedder.embed(f.text)
    mem.fact_store.upsert(f.id, f.embedding, f)
    return f


def test_renaming_a_predicate_refreshes_the_stale_display():
    """A predicate-only edit rebuilds `text`; leaving `display` behind makes every surface keep
    rendering the pre-edit wording after a save the user was told succeeded."""
    mem = Memory()
    f = _stored_outcome(mem)
    mem.update_fact(f.id, predicate="lesson")

    got = mem.fact_store.get(f.id)
    assert got.predicate == "lesson"
    assert got.display == got.object       # refreshed, not the pre-edit wording
    assert got.category == OUTCOME_CATEGORY


def test_editing_a_conclusion_keeps_it_in_the_journal():
    """classify() only knows attribute buckets, so without an explicit guard an edit silently demotes
    a conclusion out of the category the Journal and the audit skip-rule both key on."""
    mem = Memory()
    f = _stored_outcome(mem, predicate="decision", statement="读路径用混合检索：事实加原始片段")
    mem.update_fact(f.id, object="读路径用混合检索：consolidated facts 加 raw chunks")

    assert mem.fact_store.get(f.id).category == OUTCOME_CATEGORY


def _svc_with_slot(tmp_path, n=6, subject="user", predicate="occupation"):
    svc = MemoryService(data_dir=str(tmp_path))
    mem = svc.get("u")
    for i in range(n):
        junk = Fact(subject=subject, predicate=predicate, object=f"borrow from big tech {i}", user_id="u")
        junk.embedding = mem.embedder.embed(junk.text)
        mem.fact_store.upsert(junk.id, junk.embedding, junk)
    keep = Fact(subject=subject, predicate="lives_in", object="Shenzhen", user_id="u")
    keep.embedding = mem.embedder.embed(keep.text)
    mem.fact_store.upsert(keep.id, keep.embedding, keep)
    return svc, keep


def test_clearing_a_slot_erases_only_that_slot(tmp_path):
    """The audit tells the owner to clear the overflowed slot; acting on it must not touch anything else."""
    svc, keep = _svc_with_slot(tmp_path, n=6)

    result = svc.clear_slot("u", "user", "occupation", expect_count=6)
    assert result["ok"] and result["deleted"] == 6

    left = svc.memories("u", kind="attributes")["facts"]
    assert [f["id"] for f in left] == [keep.id]
    assert not [r for r in svc.audit("u")["findings"] if r["kind"] == "slot_overflow"]


def test_clearing_a_slot_refuses_when_the_count_moved(tmp_path):
    """The owner approves erasing the N rows they were shown. If the store grew since the audit ran,
    deleting the new ones too would erase more than was approved."""
    svc, _ = _svc_with_slot(tmp_path, n=6)

    result = svc.clear_slot("u", "user", "occupation", expect_count=5)
    assert result["ok"] is False and result["deleted"] == 0
    assert result["found"] == 6 and "changed" in result["reason"]
    # nothing was touched
    assert len(svc.memories("u", kind="attributes")["facts"]) == 7


def test_clearing_a_slot_is_case_insensitive_on_the_predicate(tmp_path):
    """The audit reports the predicate as stored; a client echoing it back in another case must still act."""
    svc, _ = _svc_with_slot(tmp_path, n=4)
    assert svc.clear_slot("u", "user", "Occupation", expect_count=4)["deleted"] == 4


def test_clearing_a_slot_leaves_superseded_history_alone(tmp_path):
    """The audit counts LIVE facts, so `expect_count` is a live count. If the doomed set also matched
    superseded rows the guard would compare two different populations: it would refuse forever on any
    slot that carries a supersedes chain, and — with the guard omitted — erase bi-temporal history the
    owner was never shown (CLAUDE.md §3.1: invalidate, never hard-delete, a superseded fact)."""
    import time

    svc, _ = _svc_with_slot(tmp_path, n=3)
    mem = svc.get("u")
    history = []
    for title in ("intern", "junior dev"):
        old = Fact(subject="user", predicate="occupation", object=title, user_id="u")
        old.embedding = mem.embedder.embed(old.text)
        old.invalid_at = time.time() - 60  # non-destructively invalidated by a later promotion
        mem.fact_store.upsert(old.id, old.embedding, old)
        history.append(old)

    finding = [r for r in svc.audit("u")["findings"] if r["kind"] == "slot_overflow"][0]
    assert finding["count"] == 3  # live junk only

    result = svc.clear_slot("u", finding["subject"], finding["predicate"],
                            expect_count=finding["count"])
    assert result["ok"] and result["deleted"] == 3
    assert all(mem.fact_store.get(h.id) is not None for h in history)


def test_clearing_a_slot_targets_the_same_facts_the_audit_showed_after_an_identity_link(tmp_path):
    """audit() and clear_slot() must agree about who owns a fact. Facts written before an identity link
    carry the raw handle and facts written after carry the canonical one; if the two sides resolve
    differently the count guard passes on one population while the delete runs on the other, and the
    owner loses rows they were never shown."""
    svc = MemoryService(data_dir=str(tmp_path))
    mem = svc.get("u1")
    junk = []
    for i in range(3):
        f = Fact(subject="user", predicate="occupation", object=f"borrowed title {i}", user_id="u1")
        f.embedding = mem.embedder.embed(f.text)
        mem.fact_store.upsert(f.id, f.embedding, f)
        junk.append(f)
    mem.link_identity("u1", "alice@example.com")  # canonical becomes the address
    # The slot must straddle BOTH spellings, which is the whole defect: with the two sides resolving
    # differently, the guard compares 3 against 3 and the delete runs on the other three rows. A test
    # whose junk all carries one spelling stays green even with one side reverted.
    # Extracted, not typed: source="user" facts are deliberately exempt from the group, so an
    # authoritative fact here would test the exemption instead of the identity resolution.
    canonical = mem.resolver.resolve("u1")
    assert canonical != "u1"
    after = Fact(subject="user", predicate="occupation", object="borrowed title 3", user_id=canonical)
    after.embedding = mem.embedder.embed(after.text)
    mem.fact_store.upsert(after.id, after.embedding, after)
    kept = mem.add_fact("user", "employer", "Acme", user_id="u1")  # stamped canonical

    finding = [r for r in svc.audit("u1")["findings"]
               if r["kind"] == "slot_overflow" and r["predicate"] == "occupation"][0]
    assert finding["count"] == 4
    shown = {s["fact_id"] for s in finding["samples"]}
    assert shown == {f.id for f in junk} | {after.id}

    assert svc.clear_slot("u1", "user", "occupation", expect_count=4)["deleted"] == 4
    assert all(mem.fact_store.get(f.id) is None for f in junk)
    assert mem.fact_store.get(after.id) is None
    assert mem.fact_store.get(kept.id) is not None


def test_slot_overflow_does_not_fire_on_genuinely_multi_valued_attributes(tmp_path):
    """Three children, three languages or three degrees is a person, not extraction junk. Claiming
    "单值属性不可能有 3 个值" about them is false, and the finding carries a one-click hard delete."""
    svc = MemoryService(data_dir=str(tmp_path))
    mem = svc.get("u")
    for predicate, values in (("children", ("Mia", "Leo", "Ana")),
                              ("speaks", ("Chinese", "English", "Japanese")),
                              ("graduated_from", ("Tsinghua", "MIT", "Stanford")),
                              ("occupation", ("a", "b", "c"))):
        for value in values:
            f = Fact(subject="user", predicate=predicate, object=value, user_id="u")
            f.embedding = mem.embedder.embed(f.text)
            mem.fact_store.upsert(f.id, f.embedding, f)

    flagged = {r["predicate"] for r in svc.audit("u")["findings"] if r["kind"] == "slot_overflow"}
    assert flagged == {"occupation"}


def test_a_conclusion_is_classified_for_sensitivity_like_every_other_write(tmp_path):
    """A conclusion is prose a model wrote about a work session, so it can carry a credential, a
    diagnosis or a salary. Every other write path runs classify(); if this one does not, the fact is
    born sensitive=False and lands in the share-safe `/v1/memories` and `/v1/export` views — and would
    only start being hidden AFTER the owner edits it, because update_fact does classify."""
    secret = "把生产数据库密码从 .env 换成 vault，密码不要再写进仓库"
    llm = StubLLM('[{"kind":"lesson","statement":"%s","why":"泄露过一次"}]' % secret)
    svc = _svc(tmp_path, llm)
    _session(svc)
    svc.close_session("u", "s1")

    owner = svc.memories("u", kind="outcomes", include_sensitive=True)["facts"]
    assert len(owner) == 1 and owner[0]["sensitive"] is True
    assert owner[0]["category"] == OUTCOME_CATEGORY  # classify's own bucket must not win
    assert svc.memories("u")["facts"] == []  # share-safe view hides it


def test_an_ordinary_conclusion_is_not_swept_up_by_the_sensitivity_check(tmp_path):
    """The guard above must not turn every conclusion private — that would empty the share-safe view."""
    llm = StubLLM('[{"kind":"decision","statement":"部署方式从 docker compose 换成 rsync",'
                  '"why":"compose 在共享机上会重建别人的容器"}]')
    svc = _svc(tmp_path, llm)
    _session(svc)
    svc.close_session("u", "s1")

    shared = svc.memories("u")["facts"]
    assert len(shared) == 1 and shared[0]["sensitive"] is False


def test_a_slot_clear_never_touches_a_row_the_owner_already_fixed(tmp_path):
    """The Health page invites both moves on the same slot: fix a row in place, then clear the rest.
    An in-place fix marks that row source="user" without superseding its siblings, so the two flows
    meet — and without an exemption the second move erases the work the first one just did.

    audit() and clear_slot() must apply the exemption identically: a mismatch is how the count guard
    passes on one population while the delete runs on another.
    """
    svc = MemoryService(data_dir=str(tmp_path))
    mem = svc.get("u")
    rows = []
    for i in range(6):
        f = Fact(subject="user", predicate="occupation", object=f"borrowed title {i}", user_id="u")
        f.embedding = mem.embedder.embed(f.text)
        mem.fact_store.upsert(f.id, f.embedding, f)
        rows.append(f)

    # ...the owner fixes one of them on the Health page.
    svc.update_fact("u", rows[0].id, object="founder of Engram")
    assert mem.fact_store.get(rows[0].id).source == "user"

    finding = [r for r in svc.audit("u")["findings"] if r["kind"] == "slot_overflow"][0]
    assert finding["count"] == 5  # the fixed row is no longer counted as junk

    assert svc.clear_slot("u", "user", "occupation", expect_count=5)["deleted"] == 5
    survivor = mem.fact_store.get(rows[0].id)
    assert survivor is not None and survivor.object == "founder of Engram"
