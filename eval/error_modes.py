"""What kind of wrong is each wrong answer?

An accuracy number says how many questions were missed; it does not say whether the system had nothing
to say, said a number that was off by one, or confidently said the wrong thing. Those need different
mechanisms, and without separating them a proposal is aimed at an average rather than a failure.

Run against a committed log — free, no API keys — before designing anything:

    python3 eval/error_modes.py results/longmemeval_s_engram_lean_v2_final.jsonl --system engram_lean

The modes are deliberately few and mechanical, so the classification is reproducible rather than a
judgement call:

  * **abstained** — the system declined ("I don't know"). Evidence was retrieved badly or not at all.
  * **numeric** — the gold answer contains a number and so did the prediction. Counting, dates,
    durations, quantities: retrieval may have been fine and the arithmetic or the aggregation was not.
  * **wrong value** — a confident answer that was simply not the right one.

It also reports how much each mode is *worth*: eliminating a mode entirely moves the overall score by a
known number of points, which is what decides whether attacking it can be measured at all.
"""
from __future__ import annotations

import argparse
import collections
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.compare import load  # noqa: E402
from eval.significance import minimum_detectable_effect  # noqa: E402

__all__ = ["classify", "attribute", "ABSTAIN_RE"]

# Matched against the prediction. Anchored forms first so a mere mention of "unknown" inside a real
# answer is not counted as a refusal.
ABSTAIN_RE = re.compile(
    r"^\s*(i\s+don'?t\s+know|i\s+do\s+not\s+know|unknown|n/?a|none)\b"
    r"|don'?t\s+have\s+(that|this|it|any)"
    r"|(not|isn'?t)\s+(in|mentioned\s+in)\s+(my\s+)?(memory|the\s+memory|our\s+conversations?)"
    r"|no\s+information\s+(about|on|regarding)"
    r"|cannot\s+(determine|tell|find)"
    r"|记忆里(暂时)?没有|不知道|无法确定|没有(相关|提到)",
    re.IGNORECASE,
)
_HAS_DIGIT = re.compile(r"\d")


def classify(pred: str, gold: str) -> str:
    """One of: abstained | numeric | wrong_value."""
    pred = (pred or "").strip()
    gold = (gold or "").strip()
    if ABSTAIN_RE.search(pred):
        return "abstained"
    if _HAS_DIGIT.search(gold) and _HAS_DIGIT.search(pred):
        return "numeric"
    return "wrong_value"


def _leading_number(text: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", text or "")
    return float(match.group()) if match else None


def attribute(log: dict, system: str) -> dict:
    """Per-category failure modes, plus the direction of numeric errors.

    The direction matters more than it looks: a systematic undercount points at missing evidence, which
    recall expansion can fix. Errors in both directions point at the counting itself, which is a
    different and harder target — so getting this wrong sends a mechanism after the wrong problem.
    """
    by_category: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    totals: dict[str, int] = collections.Counter()
    scored: dict[str, int] = collections.Counter()
    direction = collections.Counter()
    examples: dict[str, list] = collections.defaultdict(list)
    # Context size per outcome. A refusal on a context as large as the ones that answered correctly is
    # not retrieval starvation — the evidence budget was spent, and something after retrieval failed.
    tokens: dict[str, list[int]] = collections.defaultdict(list)

    for qid, entry in log.items():
        # Same split report.py makes: `_abs` items are the benchmark's *unanswerable* variants, graded
        # by a different judge, and on those a refusal is the correct answer. Folding them into their
        # base category counts correct behaviour as a failure mode and aims mechanisms at the wrong
        # target — which is exactly what the first version of this tool did.
        category = "abstention" if str(qid).endswith("_abs") else entry.get("_cat", "?")
        result = entry.get(system)
        if not result or result.get("err"):
            continue
        scored[category] += 1
        context_tokens = int(result.get("tok") or 0)
        if result.get("ok"):
            tokens["correct"].append(context_tokens)
            continue
        pred, gold = result.get("pred") or "", result.get("gold") or ""
        mode = classify(pred, gold)
        tokens[mode].append(context_tokens)
        by_category[category][mode] += 1
        totals[mode] += 1
        if len(examples[f"{category}/{mode}"]) < 3:
            examples[f"{category}/{mode}"].append((pred[:120], gold[:120]))
        if mode == "numeric":
            a, b = _leading_number(pred), _leading_number(gold)
            if a is not None and b is not None:
                direction["under" if a < b else "over" if a > b else "equal_but_judged_wrong"] += 1

    return {
        "by_category": {k: dict(v) for k, v in by_category.items()},
        "totals": dict(totals),
        "scored": dict(scored),
        "numeric_direction": dict(direction),
        "median_context_tokens": {
            mode: statistics.median(values) for mode, values in tokens.items() if values
        },
        "examples": dict(examples),
        "n": sum(scored.values()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Classify a run's wrong answers by failure mode.")
    ap.add_argument("log")
    ap.add_argument("--system", required=True)
    ap.add_argument("--examples", action="store_true", help="print sample predictions per mode")
    args = ap.parse_args()

    report = attribute(load(args.log), args.system)
    if not report["n"]:
        print(f"no scored items for system {args.system!r}")
        return 1

    n = report["n"]
    modes = ("abstained", "numeric", "wrong_value")
    print(f"{'category':<28}{'scored':>7}{'wrong':>7}" + "".join(f"{m:>13}" for m in modes))
    print("-" * (42 + 13 * len(modes)))
    for category in sorted(report["by_category"], key=lambda c: -sum(report["by_category"][c].values())):
        counts = report["by_category"][category]
        wrong = sum(counts.values())
        print(
            f"{category:<28}{report['scored'].get(category, 0):>7}{wrong:>7}"
            + "".join(f"{counts.get(m, 0):>13}" for m in modes)
        )
    total_wrong = sum(report["totals"].values())
    print(
        f"{'TOTAL':<28}{n:>7}{total_wrong:>7}"
        + "".join(f"{report['totals'].get(m, 0):>13}" for m in modes)
    )

    direction = report["numeric_direction"]
    if direction:
        print(
            f"\nnumeric errors: {direction.get('under', 0)} under, {direction.get('over', 0)} over, "
            f"{direction.get('equal_but_judged_wrong', 0)} numerically equal but judged wrong"
        )
        if direction.get("under", 0) and direction.get("over", 0):
            ratio = direction["under"] / max(1, direction["over"])
            if 0.5 <= ratio <= 2.0:
                print(
                    "  errors go both ways, so this is not missing evidence that recall expansion would\n"
                    "  recover — the counting itself is what fails."
                )

    # What each mode is worth, against what the benchmark can actually resolve.
    floor = minimum_detectable_effect(n, 0.22)["mde_points"]
    print(f"\nfixing a mode completely would move the overall score by (floor at this size: {floor:.2f}):")
    for mode in modes:
        count = report["totals"].get(mode, 0)
        if not count:
            continue
        points = 100.0 * count / n
        verdict = "measurable" if points > floor else "BELOW THE FLOOR — unmeasurable alone"
        print(f"  {mode:<14} {count:>4} questions  =  {points:+.1f} points   {verdict}")

    medians = report.get("median_context_tokens") or {}
    if len(medians) > 1:
        print("\nmedian retrieved context, by outcome:")
        for mode, value in sorted(medians.items(), key=lambda kv: -kv[1]):
            print(f"  {mode:<14} {value:>8.0f} tokens")
        spread = (max(medians.values()) - min(medians.values())) / max(1.0, max(medians.values()))
        if spread < 0.15:
            print(
                "  Within a few percent of each other: the refusals and the misses were given as much\n"
                "  evidence as the correct answers. Whatever failed, it was not retrieval running dry."
            )

    if args.examples:
        print("\nexamples:")
        for key, samples in sorted(report["examples"].items()):
            print(f"\n  {key}")
            for pred, gold in samples:
                print(f"    pred: {pred}")
                print(f"    gold: {gold}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
