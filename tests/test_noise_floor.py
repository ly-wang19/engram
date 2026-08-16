"""Measuring the benchmark's own instability.

The floor under every accuracy claim: if re-running an unchanged configuration moves N answers, a gain
smaller than N is not observable. These tests pin that the tool reports the flips honestly and warns when
two "identical" runs differ by more than chance — which means they were not identical.
"""
from __future__ import annotations

from eval.noise_floor import compare_repeats, summarise


def _log(system: str, correct_qids: set, total: int = 100):
    return {
        f"q{i}": {system: {"ok": f"q{i}" in correct_qids}}
        for i in range(total)
    }


def test_identical_runs_report_no_flips():
    correct = {f"q{i}" for i in range(80)}
    logs = [_log("s", correct), _log("s", correct)]
    summary = summarise(compare_repeats(logs, "s"))
    assert summary["mean_flips"] == 0
    assert summary["max_accuracy_spread"] == 0.0


def test_flips_are_counted_in_both_directions():
    """With one config, a flip to wrong and a flip to right are the same phenomenon."""
    a = {f"q{i}" for i in range(80)}
    b = (a - {"q0", "q1"}) | {"q90", "q91", "q92"}
    comparisons = compare_repeats([_log("s", a), _log("s", b)], "s")
    assert comparisons[0]["flipped_to_wrong"] == 2
    assert comparisons[0]["flipped_to_right"] == 3
    assert comparisons[0]["flips"] == 5


def test_the_floor_rises_with_instability():
    """A noisier apparatus can resolve less."""
    base = {f"q{i}" for i in range(80)}
    quiet = summarise(compare_repeats([_log("s", base), _log("s", base - {"q0"} | {"q90"})], "s"))
    noisy = summarise(compare_repeats(
        [_log("s", base), _log("s", base - {f"q{i}" for i in range(10)} | {f"q{90+i}" for i in range(10)})],
        "s",
    ))
    assert noisy["mde_points"] > quiet["mde_points"]


def test_every_pair_of_runs_is_compared():
    correct = {f"q{i}" for i in range(80)}
    comparisons = compare_repeats([_log("s", correct)] * 3, "s")
    assert len(comparisons) == 3, "three runs give three pairings"


def test_runs_that_differ_by_more_than_chance_are_flagged():
    """Identical configs should not differ systematically. If they do, they were not identical."""
    a = {f"q{i}" for i in range(50)}
    b = {f"q{i}" for i in range(85)}  # a large one-directional shift
    summary = summarise(compare_repeats([_log("s", a), _log("s", b)], "s"))
    assert summary["suspicious_pairs"], "a systematic shift must be reported, not averaged away"


def test_no_comparisons_summarises_to_nothing():
    assert summarise([]) == {}
