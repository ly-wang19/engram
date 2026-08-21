"""Measuring whether a wider detail window covers a counting question's evidence.

The measurement decided a mechanism was not worth shipping, so it has to be right about the thing it
measured. The load-bearing test is `test_selection_mirrors_the_round_robin_not_top_n`: lean_context does
not take the top N sessions for the main query, it interleaves each subquery's ranks. Measuring top-N
instead would have measured a mechanism the code does not have — and reported a gain that evaporates.
"""
from __future__ import annotations

from engram.retrieve.evidence import plan_evidence
from eval.coverage_check import select_detail_sessions


class _Mem:
    """Returns a scripted ranking per query, so selection order is the only thing under test."""

    def __init__(self, by_query: dict):
        self.by_query = by_query
        self.asked: list[str] = []

    def retrieve_episodes(self, query, _user, k):
        self.asked.append(query)
        return [_Ep(sid) for sid in self.by_query.get(query, [])][:k]


class _Ep:
    def __init__(self, sid: str):
        self.id = sid
        self.session_id = sid


class _Need:
    def __init__(self, subqueries):
        self.subqueries = tuple(subqueries)


def test_selection_mirrors_the_round_robin_not_top_n():
    """Each subquery contributes its rank-1 before any contributes its rank-2."""
    mem = _Mem({
        "main": ["m1", "m2", "m3"],
        "sub-a": ["a1", "a2"],
        "sub-b": ["b1", "b2"],
    })
    chosen = select_detail_sessions(mem, {}, "main", _Need(["sub-a", "sub-b"]), n_chunks=3)
    assert chosen == ["a1", "b1", "m1"], "rank 1 of every angle comes before rank 2 of any"


def test_the_budget_is_respected():
    mem = _Mem({"main": ["m1", "m2"], "sub-a": ["a1", "a2"]})
    assert len(select_detail_sessions(mem, {}, "main", _Need(["sub-a"]), n_chunks=2)) == 2


def test_a_zero_budget_renders_nothing():
    mem = _Mem({"main": ["m1"]})
    assert select_detail_sessions(mem, {}, "main", _Need([]), n_chunks=0) == []


def test_duplicate_sessions_across_subqueries_are_not_double_counted():
    """Two angles hitting the same session must not consume two slots of the budget."""
    mem = _Mem({"main": ["s1"], "sub-a": ["s1", "s2"], "sub-b": ["s1", "s3"]})
    chosen = select_detail_sessions(mem, {}, "main", _Need(["sub-a", "sub-b"]), n_chunks=3)
    assert len(chosen) == len(set(chosen))
    assert set(chosen) == {"s1", "s2", "s3"}


def test_no_subqueries_falls_back_to_the_main_query():
    mem = _Mem({"main": ["m1", "m2"]})
    assert select_detail_sessions(mem, {}, "main", _Need([]), n_chunks=2) == ["m1", "m2"]


def test_the_cap_raises_the_budget_only_for_aggregation():
    """The knob must not widen the window for questions that are answered by one session."""
    counting = "How many model kits have I worked on or bought?"
    lookup = "Where do I live?"
    assert plan_evidence(counting, aggregation_chunk_cap=5).n_chunks > plan_evidence(counting).n_chunks
    assert plan_evidence(lookup, aggregation_chunk_cap=5).n_chunks == plan_evidence(lookup).n_chunks


def test_the_cap_is_off_by_default():
    """Measured insufficient, so the default must not quietly enable it."""
    from engram.config import Config

    assert Config().aggregation_chunk_cap == 0
    counting = "How many trips did I take?"
    assert plan_evidence(counting, aggregation_chunk_cap=0).n_chunks == plan_evidence(counting).n_chunks
