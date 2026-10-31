"""Fixed-evidence packing regressions: no LLM or embedding is needed."""
import pytest

from engram import Config, Memory
from engram.types import Episode
from engram.util import fmt_date


def raw_block(episodes):
    return "RELEVANT CONVERSATIONS (full detail):\n" + "\n\n".join(
        f"[{e.metadata.get('date') or fmt_date(e.event_time)}] (session: {e.session_id})\n{e.content}"
        for e in episodes
    )


def no_embedding(*args, **kwargs):
    pytest.fail("Packing must not call an embedder")


def fixed_memory(enabled=True, budgeting=True):
    mem = Memory(config=Config(
        evidence_planner=False, evidence_budgeting=budgeting,
        packing_summary_fallback=enabled, provenance_chunk_promotion=False,
        provenance_evidence=False, chain_evidence=False,
    ))
    user = mem.resolver.resolve("u")
    eps = [
        Episode("Routine background. " * 40, user_id=user, session_id="first",
                event_time=1700000000, summary="Routine background."),
        Episode("The launch code is ORBIT-731.", user_id=user, session_id="second",
                event_time=1700000100, summary="The launch code is ORBIT-731."),
    ]
    for ep in eps:
        mem.episodes_doc.put(ep.id, ep)
    mem.embedder.embed = no_embedding
    mem.retriever.retrieve = lambda *a, **k: ([], {})
    mem._retrieve_cold = lambda *a, **k: ([], {})
    mem._graph_related_facts = lambda *a, **k: []
    mem.retrieve_summaries = lambda *a, **k: eps
    return mem, eps


def lean(mem, chunks, budget=400):
    return mem.lean_context("launch code", user_id="u", persona=False,
                            n_summaries=2, n_facts=0, n_chunks=chunks,
                            cascade=True, char_budget=budget)


@pytest.mark.parametrize("budgeting", [True, False])
def test_more_detail_no_longer_deletes_available_summary(budgeting):
    old, _ = fixed_memory(False, budgeting)
    assert "ORBIT-731" in lean(old, 1)
    assert "ORBIT-731" not in lean(old, 2)
    candidate, _ = fixed_memory(True, budgeting)
    result = lean(candidate, 2)
    assert "ORBIT-731" in result
    assert len(result) <= 400
    assert "SESSION SUMMARIES" in result


@pytest.mark.parametrize("budgeting", [True, False])
@pytest.mark.parametrize("default_budget", [True, False])
@pytest.mark.parametrize("budget", [0, 1, 90, 400, 2000])
def test_disabled_flag_preserves_all_legacy_budget_exits(budgeting, default_budget, budget):
    mem, eps = fixed_memory(False, budgeting)
    blocks = ["FACTS:\n" + "fact " * 100, raw_block(eps)]
    assembled = "\n\n".join(blocks)
    if len(assembled) > budget and budgeting:
        expected = mem._fit_blocks_by_evidence_budget(blocks, budget, None)
    elif len(assembled) > budget and default_budget:
        expected = mem._fit_blocks_even(blocks, budget)
    else:
        expected = assembled[:budget]
    assert mem._pack_lean_blocks(blocks, budget, None, eps, default_budget=default_budget) == expected
    assert Config().packing_summary_fallback is False


def test_no_overflow_is_byte_identical_without_duplicate_summaries():
    mem, eps = fixed_memory()
    block = raw_block(eps)
    result = mem._pack_lean_blocks([block], len(block), None, eps)
    assert result == block
    assert "SESSION SUMMARIES" not in result
    assert result.count("ORBIT-731") == 1


def test_complete_raw_is_not_duplicated_even_when_other_blocks_overflow():
    mem, eps = fixed_memory()
    short = eps[1]
    blocks = [raw_block([short]), "USER PROFILE:\n" + "noise " * 400]
    result = mem._pack_lean_blocks(blocks, 400, None, [short])
    assert short.content in result
    assert "SESSION SUMMARIES" not in result
    assert result.count("ORBIT-731") == 1


def test_header_only_is_not_treated_as_complete_raw():
    mem, eps = fixed_memory()
    ep = eps[0]
    ep.content = "Opening chatter. " * 40 + "A unique tail."
    ep.summary = "The recovered summary."
    before = raw_block([ep])[:220]
    assert "session: first" in before and "A unique tail." not in before
    result = mem._pack_lean_blocks([raw_block([ep])], 400, None, [ep])
    assert ep.summary in result
    assert "A unique tail." not in result


@pytest.mark.parametrize("budgeting", [True, False])
@pytest.mark.parametrize("default_budget", [True, False])
@pytest.mark.parametrize("budget", [0, 1, 30, 80, 150, 400])
def test_fallback_never_exceeds_budget(budgeting, default_budget, budget):
    mem, eps = fixed_memory(True, budgeting)
    result = mem._pack_lean_blocks([raw_block(eps)], budget, None, eps, default_budget=default_budget)
    assert len(result) <= budget


def test_missing_or_uninformative_summary_does_not_invent_an_answer():
    mem, eps = fixed_memory()
    for ep in eps:
        ep.summary = ""
    blocks = [raw_block(eps)]
    assert mem._pack_lean_blocks(blocks, 400, None, eps) == mem._fit_blocks_by_evidence_budget(blocks, 400, None)
    eps[1].summary = "Discussed a launch."
    result = mem._pack_lean_blocks(blocks, 400, None, eps)
    assert "Discussed a launch." in result
    assert "ORBIT-731" not in result


def test_even_packing_rechecks_newly_truncated_raw_with_bounded_passes(monkeypatch):
    mem, _ = fixed_memory(True, False)
    first = Episode("A" * 100, session_id="a", event_time=1700000000, summary="FIRST SUMMARY")
    second = Episode("B" * 500, session_id="b", event_time=1700000100, summary="SECOND SUMMARY")
    calls = []
    original = mem._fit_blocks_even

    def capture(blocks, budget):
        calls.append(list(blocks))
        return original(blocks, budget)

    monkeypatch.setattr(mem, "_fit_blocks_even", capture)
    # Initially A is complete and B is cut; adding B's summary halves A's quota.
    result = mem._pack_lean_blocks([raw_block([first, second])], 240, None,
                                   [first, second], default_budget=True)
    assert 1 < len(calls) <= 3
    assert "FIRST SUMMARY" in calls[-1][0]
    assert "SECOND SUMMARY" in calls[-1][0]
    assert len(result) <= 240


def test_fixed_snapshot_entrypoint_does_not_mutate_blocks_or_episodes():
    mem, eps = fixed_memory()
    blocks = [raw_block(eps)]
    original = list(blocks)
    summaries = [e.summary for e in eps]
    mem._pack_lean_blocks(blocks, 400, None, eps)
    assert blocks == original and [e.summary for e in eps] == summaries


def test_existing_summary_tail_is_not_evicted_by_a_longer_fallback_heading():
    mem, eps = fixed_memory()
    heading = "SESSION SUMMARIES (relevant, chronological):\n"
    marker = "TAIL_MARKER"
    # Put the marker exactly at the preexisting 140-character summary quota.
    summary = heading + "x" * (140 - len(heading) - len(marker)) + marker
    blocks = [summary, raw_block(eps)]
    mem.config.packing_summary_fallback = False
    before = mem._pack_lean_blocks(blocks, 400, None, eps)
    assert marker in before
    mem.config.packing_summary_fallback = True
    after = mem._pack_lean_blocks(blocks, 400, None, eps)
    assert marker in after
    assert after.count("SESSION SUMMARIES") == 1
    assert "chronological" not in after
    assert len(after) <= 400


def test_equal_dates_keep_input_order_independent_of_episode_ids():
    mem, eps = fixed_memory()
    eps[1].event_time = eps[0].event_time
    blocks = [raw_block(eps)]
    first = mem._pack_lean_blocks(blocks, 400, None, eps)
    eps[0].id, eps[1].id = "zz-last-uuid", "aa-first-uuid"
    second = mem._pack_lean_blocks(blocks, 400, None, eps)
    assert first == second
