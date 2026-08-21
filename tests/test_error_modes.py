"""Classifying wrong answers by failure mode.

An accuracy number says how many were missed, not what kind of wrong they were — and abstaining, being
off by one, and confidently answering wrong need different mechanisms. The load-bearing test here is
`test_unanswerable_items_are_not_counted_as_abstention_failures`: the benchmark's `_abs` items are
graded by an unanswerable judge, so refusing them is correct, and folding them into their base category
turns right behaviour into a failure mode and aims the next mechanism at nothing.
"""
from __future__ import annotations

from eval.error_modes import attribute, classify


def test_refusals_are_recognised_in_both_languages():
    for text in ("I don't know", "i do not know.", "That's not in my memory", "记忆里暂时没有这条",
                 "No information about that", "Unknown"):
        assert classify(text, "42") == "abstained", text


def test_a_refusal_word_inside_a_real_answer_is_not_a_refusal():
    """'unknown' appearing in an answer is not the system declining to answer."""
    assert classify("The scale is unknown for two of the kits, the rest are 1:48", "1:48") != "abstained"


def test_a_numeric_answer_against_a_numeric_gold_is_a_counting_failure():
    assert classify("3", "4") == "numeric"
    assert classify("11 weeks and 4 days", "15") == "numeric"


def test_a_confident_non_numeric_miss_is_a_wrong_value():
    assert classify("a jazz quartet", "a bluegrass band") == "wrong_value"


def _log(rows):
    """rows: (qid, cat, ok, pred, gold)"""
    return {
        qid: {"_cat": cat, "s": {"ok": ok, "pred": pred, "gold": gold}}
        for qid, cat, ok, pred, gold in rows
    }


def test_unanswerable_items_are_not_counted_as_abstention_failures():
    """`_abs` items are the benchmark's unanswerable variants; a refusal there is the right answer, so
    they belong in their own bucket rather than inflating the category they came from."""
    log = _log([
        ("q1", "temporal-reasoning", False, "I don't know", "4 days"),
        ("q2_abs", "temporal-reasoning", False, "I don't know", "unanswerable"),
    ])
    report = attribute(log, "s")
    assert report["by_category"]["temporal-reasoning"] == {"abstained": 1}
    assert report["by_category"]["abstention"] == {"abstained": 1}


def test_correct_answers_are_not_classified():
    log = _log([("q1", "multi-session", True, "4", "4"), ("q2", "multi-session", False, "3", "4")])
    report = attribute(log, "s")
    assert report["scored"]["multi-session"] == 2
    assert sum(report["by_category"]["multi-session"].values()) == 1


def test_errored_items_are_excluded_from_the_denominator():
    """An error is missing data; counting it as scored would understate accuracy and the mode split."""
    log = {"q1": {"_cat": "c", "s": {"err": "timeout"}}, "q2": {"_cat": "c", "s": {"ok": True}}}
    report = attribute(log, "s")
    assert report["n"] == 1


def test_numeric_direction_is_reported():
    """A one-sided miss means missing evidence; a two-sided one means the counting itself fails, and the
    two call for different mechanisms."""
    log = _log([
        ("q1", "multi-session", False, "3", "4"),
        ("q2", "multi-session", False, "5", "4"),
        ("q3", "multi-session", False, "2", "4"),
    ])
    direction = attribute(log, "s")["numeric_direction"]
    assert direction["under"] == 2
    assert direction["over"] == 1


def test_empty_log_reports_nothing_rather_than_dividing_by_zero():
    assert attribute({}, "s")["n"] == 0


def test_context_size_is_reported_per_outcome():
    """A refusal on as much evidence as the correct answers got is not retrieval running dry — the
    distinction decides whether the next mechanism belongs before or after retrieval."""
    log = {
        "q1": {"_cat": "c", "s": {"ok": True, "tok": 9600}},
        "q2": {"_cat": "c", "s": {"ok": False, "pred": "I don't know", "gold": "4 days", "tok": 9650}},
        "q3": {"_cat": "c", "s": {"ok": False, "pred": "3", "gold": "4", "tok": 9500}},
    }
    medians = attribute(log, "s")["median_context_tokens"]
    assert medians["correct"] == 9600
    assert medians["abstained"] == 9650
    assert medians["numeric"] == 9500


def test_missing_token_counts_do_not_break_the_report():
    log = {"q1": {"_cat": "c", "s": {"ok": False, "pred": "x", "gold": "y"}}}
    assert attribute(log, "s")["median_context_tokens"]["wrong_value"] == 0
