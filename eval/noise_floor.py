"""Measure how much the benchmark moves when nothing changes.

The answerer is not deterministic at temperature 0: run an identical configuration twice and some
answers flip. Everyone working on this repo knows that as "roughly 6-10 of 500", which is an
observation, not a measurement — and the difference matters, because that number is the floor under
every accuracy claim the project can make. A mechanism that gains less than the floor cannot be shown
to work, however many times it is re-run.

This turns the observation into a committed number. Feed it two or more runs of the *same* config and
it reports how many answers flipped, in which direction, and what the smallest trustworthy gain is as a
consequence.

    # produce the inputs (identical flags, different --out) — this is the part that costs money
    python3 eval/bench.py --data s --limit 500 --systems engram_lean \
        --answerer volcano:doubao-seed-2-0-pro-260215 --judge volcano:deepseek-v3-2-251201 \
        --extractor volcano:doubao-seed-1-6-flash-250615 --embedder bge-small --reasoning --persona \
        --chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28 \
        --out results/noise_repeat_1.jsonl
    # ... same command again with --out results/noise_repeat_2.jsonl

    python3 eval/noise_floor.py results/noise_repeat_1.jsonl results/noise_repeat_2.jsonl \
        --system engram_lean

A flip is not a bug and not a regression. It is the measurement apparatus, and this is its error bar.
"""
from __future__ import annotations

import argparse
import itertools
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.compare import load  # noqa: E402
from eval.significance import (  # noqa: E402
    mcnemar_exact,
    minimum_detectable_effect,
    paired_outcomes,
)

__all__ = ["compare_repeats", "summarise"]


def compare_repeats(logs: list[dict], system: str) -> list[dict]:
    """Every pairing of identical-config runs, with what changed between them."""
    out = []
    for (index_a, log_a), (index_b, log_b) in itertools.combinations(enumerate(logs), 2):
        pairs = paired_outcomes(log_a, log_b, system, system)
        result = mcnemar_exact(pairs)
        out.append({
            "a": index_a,
            "b": index_b,
            "n": result["n"],
            # Direction is reported but should NOT be read as one run being better: with the same
            # config, both directions are the same phenomenon.
            "flipped_to_wrong": result["only_a"],
            "flipped_to_right": result["only_b"],
            "flips": result["discordant"],
            "flip_rate": result["discordant"] / result["n"] if result["n"] else 0.0,
            "acc_a": result["acc_a"],
            "acc_b": result["acc_b"],
            "spread": abs(result["acc_a"] - result["acc_b"]),
            "p_value": result["p_value"],
        })
    return out


def summarise(comparisons: list[dict]) -> dict:
    """The floor: the flip rate across repeats, and the gain it makes unmeasurable."""
    if not comparisons:
        return {}
    flip_rates = [c["flip_rate"] for c in comparisons]
    spreads = [c["spread"] for c in comparisons]
    n = max(c["n"] for c in comparisons)
    worst_rate = max(flip_rates)
    plan = minimum_detectable_effect(n, worst_rate)
    return {
        "runs_compared": len(comparisons),
        "items": n,
        "mean_flip_rate": statistics.fmean(flip_rates),
        "worst_flip_rate": worst_rate,
        "mean_flips": statistics.fmean([c["flips"] for c in comparisons]),
        "max_accuracy_spread": max(spreads),
        "mde_points": plan["mde_points"],
        # A same-config difference that reads as "significant" means the runs differ by more than
        # chance -- i.e. the configs were not actually identical, or something else changed.
        "suspicious_pairs": [c for c in comparisons if c["p_value"] < 0.05],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="How much does the benchmark move when nothing changes?")
    ap.add_argument("logs", nargs="+", help="two or more runs of the SAME configuration")
    ap.add_argument("--system", required=True, help="system name present in every log")
    args = ap.parse_args()

    if len(args.logs) < 2:
        ap.error("need at least two runs of the same configuration")

    logs = [load(path) for path in args.logs]
    comparisons = compare_repeats(logs, args.system)
    if not any(c["n"] for c in comparisons):
        print("no questions scored by both runs — are these the same benchmark?")
        return 1

    names = [Path(p).name for p in args.logs]
    print(f"system: {args.system}")
    for i, name in enumerate(names):
        print(f"  run {i}: {name}")

    print(f"\n{'pair':>8}  {'items':>6}  {'flips':>6}  {'rate':>6}  {'acc A':>7}  {'acc B':>7}  {'spread':>7}")
    print("-" * 60)
    for c in comparisons:
        print(
            f"{c['a']}↔{c['b']:<6}  {c['n']:>6}  {c['flips']:>6}  {c['flip_rate']:>5.1%}  "
            f"{c['acc_a']:>6.1%}  {c['acc_b']:>6.1%}  {c['spread']:>6.1%}"
        )

    summary = summarise(comparisons)
    print(
        f"\nre-running the same configuration flips {summary['mean_flips']:.0f} of "
        f"{summary['items']} answers on average ({summary['mean_flip_rate']:.1%}); the widest gap "
        f"between two identical runs is {summary['max_accuracy_spread']*100:.1f} points."
    )
    print(
        f"\nTHE FLOOR: a claimed gain below {summary['mde_points']:.2f} points cannot be told apart "
        f"from re-running the same configuration."
    )
    if summary["suspicious_pairs"]:
        print(
            "\nWARNING: some pairs differ by more than chance (p < 0.05). Runs of an identical config "
            "should not. Check that the flags, dataset slice and model versions really were the same."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
