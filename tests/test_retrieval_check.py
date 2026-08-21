"""Was the answer's session retrieved, and how highly?

The finding this guards is that 73% of failures had the answer session in the full-detail window and
still failed — which retires every mechanism aimed at retrieving more. That conclusion rests on the rank
being right, so `test_rank_is_the_position_of_the_first_answer_session` is the load-bearing test: a
membership-only check would have said "retrieved" for a session shown as a one-line summary, and pointed
the next mechanism at the wrong layer.
"""
from __future__ import annotations

from eval.retrieval_check import failure_modes


class _Embedder:
    """Deterministic stand-in: no model download, no API."""

    def embed(self, text: str) -> list[float]:
        return [float(len(text) % 7), float(text.count("a")), 1.0]


def test_failure_modes_skips_correct_and_errored_answers():
    """Only wrong answers are diagnosed; an errored item is missing data, not a failure to explain."""
    import json
    import tempfile

    rows = [
        {"qid": "q1", "cat": "c", "sys": {"s": {"ok": True, "pred": "4", "gold": "4"}}},
        {"qid": "q2", "cat": "c", "sys": {"s": {"ok": False, "pred": "I don't know", "gold": "4"}}},
        {"qid": "q3", "cat": "c", "sys": {"s": {"err": "timeout"}}},
        {"qid": "q4", "cat": "c", "sys": {"s": {"ok": False, "pred": "3", "gold": "4"}}},
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
        path = fh.name

    modes = failure_modes(path, "s")
    assert modes == {"q2": "abstained", "q4": "numeric"}


def _item(answer_index: int, sessions: int = 5) -> dict:
    """A haystack where one session obviously answers the question."""
    return {
        "question_id": "q1",
        "question_type": "temporal-reasoning",
        "question": "aaaaaaaa",  # the embedder above scores on 'a' count, so this ranks the marked one
        "haystack_session_ids": [f"s{i}" for i in range(sessions)],
        "haystack_sessions": [
            [{"role": "user", "content": "aaaaaaaa" if i == answer_index else f"filler {i}"}]
            for i in range(sessions)
        ],
        "answer_session_ids": [f"s{answer_index}"],
    }


def test_rank_is_the_position_of_the_first_answer_session():
    """Membership alone cannot distinguish 'shown in full' from 'compressed to a summary line'."""
    from eval.retrieval_check import check_item

    row = check_item(_item(answer_index=0), _Embedder(), k_sessions=5)
    assert row["hit"] is True
    assert row["rank"] == 1


def test_a_missing_answer_session_reports_no_rank():
    from eval.retrieval_check import check_item

    item = _item(answer_index=0)
    item["answer_session_ids"] = ["nowhere"]
    row = check_item(item, _Embedder(), k_sessions=5)
    assert row["hit"] is False
    assert row["rank"] is None


def test_a_narrow_slice_can_miss_a_session_a_wide_one_finds():
    """k is the width of the slice being tested, so it must actually bound what comes back."""
    from eval.retrieval_check import check_item

    row = check_item(_item(answer_index=0, sessions=8), _Embedder(), k_sessions=1)
    assert row["retrieved"] <= 1


def test_coverage_counts_all_answer_sessions_not_just_the_first():
    """The correction this file exists to hold.

    A counting question whose answer spans four sessions cannot be answered from the one that ranked
    highest. Reporting only the first hit said "the evidence was retrieved" for exactly the questions
    that could not possibly be counted correctly.
    """
    from eval.retrieval_check import check_item

    item = _item(answer_index=0, sessions=5)
    item["answer_session_ids"] = ["s0", "s3"]  # answer spans two sessions, only one ranks first
    row = check_item(item, _Embedder(), k_sessions=5)

    assert row["answer_sessions"] == 2
    assert row["rank"] == 1, "the top-ranked answer session is still found"
    assert row["covered_top2"] < row["answer_sessions"], (
        "and coverage must show that the full-detail window did not hold all of them"
    )
    assert row["covered_all"] == 2, "both were retrieved somewhere in the slice"
