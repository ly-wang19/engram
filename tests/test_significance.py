"""Telling a real benchmark gain from the answerer's noise.

An identical configuration re-run on LongMemEval_S moves 6-10 of 500 answers, so a bare accuracy
difference smaller than that is not evidence of anything. These tests pin the instrument that decides:
the paired test must ignore the questions both systems answered the same way, must not call a coin-flip
split significant, and must report how small a gain the run could have resolved at all.
"""
from __future__ import annotations

import pytest

from eval.significance import (
    bootstrap_difference,
    mcnemar_exact,
    minimum_detectable_effect,
    paired_outcomes,
    verdict,
)


def _pairs(both: int, only_a: int, only_b: int, neither: int):
    out = []
    for i in range(both):
        out.append((f"both{i}", True, True))
    for i in range(only_a):
        out.append((f"a{i}", True, False))
    for i in range(only_b):
        out.append((f"b{i}", False, True))
    for i in range(neither):
        out.append((f"n{i}", False, False))
    return out


# --- McNemar ---


def test_agreements_carry_no_weight():
    """The heart of the paired test: questions both systems get right or wrong say nothing about which
    is better, so adding a thousand of them must not change the verdict."""
    small = mcnemar_exact(_pairs(both=10, only_a=2, only_b=8, neither=10))
    padded = mcnemar_exact(_pairs(both=1000, only_a=2, only_b=8, neither=1000))
    assert small["p_value"] == padded["p_value"]


def test_a_coin_flip_split_is_not_significant():
    """Equal disagreements in both directions is exactly what two equivalent systems produce."""
    result = mcnemar_exact(_pairs(both=400, only_a=25, only_b=25, neither=50))
    assert result["p_value"] == 1.0
    assert result["difference"] == 0.0


def test_a_lopsided_split_is_significant():
    result = mcnemar_exact(_pairs(both=337, only_a=29, only_b=81, neither=53))
    assert result["p_value"] < 0.001
    assert result["difference"] > 0, "positive means B is ahead"


def test_a_small_lead_over_few_disagreements_is_not_significant():
    """The regression this whole module exists to prevent: +3 points that chance explains."""
    result = mcnemar_exact(_pairs(both=329, only_a=51, only_b=66, neither=54))
    assert result["difference"] == pytest.approx(0.03, abs=0.005)
    assert result["p_value"] > 0.05, "a 15-question edge over 117 disagreements is noise"


def test_identical_runs_report_no_evidence():
    result = mcnemar_exact(_pairs(both=450, only_a=0, only_b=0, neither=50))
    assert result["discordant"] == 0
    assert result["p_value"] == 1.0
    assert "IDENTICAL" in verdict(result, {"low": 0.0, "high": 0.0})


def test_accuracy_and_difference_match_the_counts():
    result = mcnemar_exact(_pairs(both=337, only_a=29, only_b=81, neither=53))
    assert result["n"] == 500
    assert result["acc_a"] == pytest.approx((337 + 29) / 500)
    assert result["acc_b"] == pytest.approx((337 + 81) / 500)
    assert result["difference"] == pytest.approx((81 - 29) / 500)


def test_empty_input_does_not_explode():
    result = mcnemar_exact([])
    assert result["n"] == 0
    assert result["p_value"] == 1.0


# --- bootstrap ---


def test_interval_brackets_the_observed_difference():
    pairs = _pairs(both=337, only_a=29, only_b=81, neither=53)
    result = mcnemar_exact(pairs)
    interval = bootstrap_difference(pairs, iterations=2000, seed=1)
    assert interval["low"] < result["difference"] < interval["high"]


def test_interval_is_reproducible_from_its_seed():
    """A reported interval that cannot be recomputed is not evidence."""
    pairs = _pairs(both=300, only_a=40, only_b=60, neither=100)
    first = bootstrap_difference(pairs, iterations=1000, seed=7)
    second = bootstrap_difference(pairs, iterations=1000, seed=7)
    assert first == second
    assert bootstrap_difference(pairs, iterations=1000, seed=8) != first


def test_a_non_significant_difference_has_an_interval_spanning_zero():
    pairs = _pairs(both=329, only_a=51, only_b=66, neither=54)
    interval = bootstrap_difference(pairs, iterations=3000, seed=0)
    assert interval["low"] < 0 < interval["high"], "if zero is plausible, the claim is not established"


# --- planning ---


def test_more_items_resolve_smaller_gains():
    small = minimum_detectable_effect(500, 0.08)["mde_points"]
    large = minimum_detectable_effect(5000, 0.08)["mde_points"]
    assert large < small


def test_only_disagreements_count_toward_resolution():
    """Two systems that mostly agree have little to learn from, however many items were run."""
    assert minimum_detectable_effect(500, 0.02)["discordant_items"] == pytest.approx(10)
    assert minimum_detectable_effect(500, 0.20)["discordant_items"] == pytest.approx(100)


def test_too_few_disagreements_resolves_nothing():
    assert minimum_detectable_effect(100, 0.001)["mde_points"] == float("inf")


def test_a_500_item_run_cannot_resolve_a_one_point_gain():
    """The number that matters for this project: the contested gap to the top of the leaderboard is
    smaller than what a run of this size can distinguish from chance."""
    mde = minimum_detectable_effect(500, 0.08)["mde_points"]
    assert mde > 1.6, "a 1.6-point gap is below this benchmark's resolution at n=500"


# --- pairing ---


def test_only_questions_both_runs_scored_are_compared():
    """Comparing over different question sets compares two different exams."""
    log_a = {
        "q1": {"engram_lean": {"ok": True}},
        "q2": {"engram_lean": {"ok": True}},
        "q3": {"engram_lean": {"ok": False}},
    }
    log_b = {
        "q1": {"engram_lean": {"ok": False}},
        "q2": {"engram_lean": {"ok": True}},
    }
    pairs = paired_outcomes(log_a, log_b, "engram_lean", "engram_lean")
    assert {qid for qid, _a, _b in pairs} == {"q1", "q2"}


def test_errored_items_are_excluded_not_counted_wrong():
    """An errored item is missing data. Scoring it as wrong would penalise whichever system crashed on
    the hardest questions, which is backwards."""
    log_a = {"q1": {"s": {"ok": True}}, "q2": {"s": {"err": "timeout"}}}
    log_b = {"q1": {"s": {"ok": False}}, "q2": {"s": {"ok": True}}}
    pairs = paired_outcomes(log_a, log_b, "s", "s")
    assert [qid for qid, _a, _b in pairs] == ["q1"]


def test_abstentions_are_scored_as_wrong_not_dropped():
    """An abstention is an answer the system gave and the judge rejected — real data, unlike an error."""
    log_a = {"q1": {"s": {"ok": False}}}
    log_b = {"q1": {"s": {"ok": True}}}
    pairs = paired_outcomes(log_a, log_b, "s", "s")
    assert pairs == [("q1", False, True)]
