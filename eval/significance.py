"""Is a benchmark difference real, or is it the answerer's noise?

Re-running an identical configuration on LongMemEval_S moves 6-10 of 500 answers, because the answerer is
not deterministic even at temperature 0. Any single-run comparison smaller than that is indistinguishable
from chance — which means "83.6 to 84.4, we improved" is not a finding, and a long list of mechanisms was
already retired on exactly that basis.

This module is the instrument that tells the two apart. Three questions, in the order they matter:

  1. **Is the observed difference significant?** McNemar's exact test, which is the right test for two
     systems scored on the *same* items: it ignores the questions both got right or both got wrong (they
     carry no information about which is better) and asks whether the disagreements lean one way more
     than a coin would.
  2. **How large could the true difference plausibly be?** A bootstrap interval over the paired
     per-question outcomes. A p-value alone hides that a "win" may be consistent with anything from -1 to
     +4 points.
  3. **Could this experiment have detected the gain I am hoping for?** The minimum detectable effect,
     from the item count and how often the systems disagree. Answered *before* spending on a run, it is
     the difference between an experiment and an expense — and the honest answer is sometimes that the
     benchmark cannot resolve what is being attempted.

Pure stdlib: an exact binomial tail, not a chi-square approximation, because the discordant counts here
are small enough that the approximation is wrong exactly where the decision is closest.

    python3 eval/significance.py results/a.jsonl results/b.jsonl --system engram_lean
    python3 eval/significance.py --plan 500 --discordance 0.08   # what could a 500-item run detect?
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.compare import _ok, _scored, load  # noqa: E402

__all__ = ["mcnemar_exact", "bootstrap_difference", "minimum_detectable_effect", "paired_outcomes"]


def paired_outcomes(
    log_a: dict, log_b: dict, system_a: str, system_b: str
) -> list[tuple[str, bool, bool]]:
    """(qid, a_correct, b_correct) for questions BOTH runs actually scored.

    Restricting to the intersection is not tidiness: comparing over different question sets compares two
    different exams, and a system that errored on its hardest items would look better for it.
    """
    shared = []
    for qid, entry_a in log_a.items():
        entry_b = log_b.get(qid)
        if entry_b is None:
            continue
        if not (_scored(entry_a, system_a) and _scored(entry_b, system_b)):
            continue
        shared.append((qid, _ok(entry_a, system_a), _ok(entry_b, system_b)))
    return shared


def _binom_tail(k: int, n: int) -> float:
    """P(X <= k) for X ~ Binomial(n, 0.5), computed exactly."""
    if n == 0:
        return 1.0
    return sum(math.comb(n, i) for i in range(k + 1)) / (2**n)


def mcnemar_exact(pairs: list[tuple[str, bool, bool]]) -> dict:
    """Exact McNemar test over paired outcomes.

    Only the disagreements carry information. `b` is where A is right and B is wrong, `c` the reverse; if
    the two systems were equally good, each disagreement is a coin flip, so the test asks how unlikely
    the observed split is. Questions both systems answer the same way are excluded by construction —
    which is why this is far more sensitive than comparing two accuracy percentages.
    """
    both = only_a = only_b = neither = 0
    for _qid, a_ok, b_ok in pairs:
        if a_ok and b_ok:
            both += 1
        elif a_ok:
            only_a += 1
        elif b_ok:
            only_b += 1
        else:
            neither += 1

    discordant = only_a + only_b
    smaller = min(only_a, only_b)
    # Two-sided exact p: both tails of a fair coin, capped at 1 for the symmetric case.
    p_value = min(1.0, 2.0 * _binom_tail(smaller, discordant)) if discordant else 1.0
    n = len(pairs)
    return {
        "n": n,
        "both_correct": both,
        "only_a": only_a,
        "only_b": only_b,
        "neither": neither,
        "discordant": discordant,
        "acc_a": (both + only_a) / n if n else 0.0,
        "acc_b": (both + only_b) / n if n else 0.0,
        "difference": (only_b - only_a) / n if n else 0.0,  # positive = B better
        "p_value": p_value,
    }


def bootstrap_difference(
    pairs: list[tuple[str, bool, bool]], iterations: int = 10_000, seed: int = 0, alpha: float = 0.05
) -> dict:
    """Percentile bootstrap interval for B's accuracy minus A's, resampling questions in pairs.

    Resampling the *pair* keeps each question's two outcomes together, which is what makes the interval
    reflect the paired design rather than treating the runs as independent samples.
    """
    if not pairs:
        return {"low": 0.0, "high": 0.0, "iterations": 0}
    rng = random.Random(seed)  # seeded so a reported interval can be reproduced exactly
    n = len(pairs)
    diffs = []
    for _ in range(iterations):
        total = 0
        for _ in range(n):
            _qid, a_ok, b_ok = pairs[rng.randrange(n)]
            total += int(b_ok) - int(a_ok)
        diffs.append(total / n)
    diffs.sort()
    lo_index = int((alpha / 2) * iterations)
    hi_index = min(iterations - 1, int((1 - alpha / 2) * iterations))
    return {"low": diffs[lo_index], "high": diffs[hi_index], "iterations": iterations}


def minimum_detectable_effect(
    n_items: int, discordance: float, alpha: float = 0.05, power: float = 0.80
) -> dict:
    """The smallest true accuracy gain a run of this size could detect.

    Asked before a run, this is the difference between an experiment and an expense. With `n_items`
    questions and the two systems disagreeing on a `discordance` fraction of them, only the disagreements
    are informative, so the resolving power comes from `n_items * discordance` — usually far fewer items
    than the benchmark's headline count suggests.

    Normal approximation on the discordant-pair proportion. Deliberately not exact: the answer is used to
    decide whether to spend on a run, and a figure good to a fraction of a point is enough for that.
    """
    discordant = max(0.0, n_items * discordance)
    if discordant < 1:
        return {"discordant_items": discordant, "mde_points": float("inf")}
    # z for a two-sided alpha and the requested power.
    z_alpha = _z_for(1 - alpha / 2)
    z_power = _z_for(power)
    # Detectable imbalance in the discordant split, converted back to overall accuracy points.
    imbalance = (z_alpha + z_power) / (2 * math.sqrt(discordant))
    return {
        "discordant_items": discordant,
        "mde_points": 100.0 * imbalance * discordance,
    }


def _z_for(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation; accurate to ~1e-9 here)."""
    if not 0.0 < p < 1.0:
        raise ValueError("probability must be in (0, 1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > p_high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def verdict(result: dict, interval: dict, alpha: float = 0.05) -> str:
    """One line a person can act on, rather than a p-value to be interpreted optimistically."""
    if result["discordant"] == 0:
        return "IDENTICAL — the two runs agree on every scored question."
    if result["p_value"] > alpha:
        return (
            f"NOT DISTINGUISHABLE (p={result['p_value']:.3f}) — this difference is what chance produces. "
            f"The true gap is somewhere in [{interval['low']*100:+.1f}, {interval['high']*100:+.1f}] points."
        )
    b_ahead = result["difference"] > 0
    direction = "B beats A" if b_ahead else "A beats B"
    # The interval is stated B-minus-A. When A is the winner the sentence names A first, so the interval
    # has to be reoriented to match — otherwise it reads "A beats B by 10 points, plausibly [-13, -7]",
    # and a reader cannot tell whether the effect is positive or negative.
    low, high = (
        (interval["low"] * 100, interval["high"] * 100)
        if b_ahead
        else (-interval["high"] * 100, -interval["low"] * 100)
    )
    return (
        f"SIGNIFICANT (p={result['p_value']:.4f}) — {direction} by {abs(result['difference'])*100:.1f} "
        f"points, plausibly [{low:+.1f}, {high:+.1f}]."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Is a benchmark difference real, or is it noise?")
    ap.add_argument("logs", nargs="*", help="two result JSONL logs to compare")
    ap.add_argument("--system", default=None, help="system name in both logs")
    ap.add_argument("--system-a", default=None)
    ap.add_argument("--system-b", default=None)
    ap.add_argument("--iterations", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--plan", type=int, default=0, help="item count to plan for, instead of comparing")
    ap.add_argument("--discordance", type=float, default=0.08,
                    help="expected fraction of items the two systems answer differently")
    args = ap.parse_args()

    if args.plan:
        for rate in sorted({args.discordance, 0.05, 0.10, 0.20}):
            plan = minimum_detectable_effect(args.plan, rate)
            print(
                f"n={args.plan:<6} discordance={rate:<5.0%} "
                f"informative items={plan['discordant_items']:>6.0f}  "
                f"smallest detectable gain={plan['mde_points']:.2f} points"
            )
        print(
            "\nOnly the questions the two systems answer differently carry information, so the usable\n"
            "sample is far smaller than the headline item count. A gain below the last column cannot be\n"
            "told from chance by a run this size, however many times it is repeated within one run."
        )
        return 0

    if len(args.logs) != 2:
        ap.error("give exactly two logs, or use --plan N")

    log_a, log_b = load(args.logs[0]), load(args.logs[1])
    system_a = args.system_a or args.system
    system_b = args.system_b or args.system
    if not (system_a and system_b):
        ap.error("--system (or --system-a/--system-b) is required")

    pairs = paired_outcomes(log_a, log_b, system_a, system_b)
    if not pairs:
        print("no questions were scored by both runs — nothing comparable")
        return 1

    result = mcnemar_exact(pairs)
    interval = bootstrap_difference(pairs, iterations=args.iterations, seed=args.seed)

    print(f"A = {Path(args.logs[0]).name} [{system_a}]")
    print(f"B = {Path(args.logs[1]).name} [{system_b}]")
    print(f"\ncompared on {result['n']} questions scored by both runs")
    print(f"  both correct   {result['both_correct']:>5}")
    print(f"  only A correct {result['only_a']:>5}")
    print(f"  only B correct {result['only_b']:>5}   <- these two are the entire evidence")
    print(f"  both wrong     {result['neither']:>5}")
    print(f"\naccuracy  A {result['acc_a']*100:.1f}%   B {result['acc_b']*100:.1f}%   "
          f"difference {result['difference']*100:+.1f} points")
    print(f"\n{verdict(result, interval)}")

    plan = minimum_detectable_effect(result["n"], result["discordant"] / max(1, result["n"]))
    print(
        f"\nAt this sample and disagreement rate, the smallest gain a run like this could have detected "
        f"is {plan['mde_points']:.2f} points."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
