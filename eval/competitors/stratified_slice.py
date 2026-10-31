"""Pick a deterministic, category-stratified slice of LongMemEval for the competitor scoreboard.

A head-to-head is only worth citing if every system saw the SAME questions, and if those questions
aren't accidentally skewed toward one category. `--limit N` on bench.py takes the first N items in file
order, which over-samples whatever the file happens to front-load. This picks N qids with each category
represented in proportion to the full benchmark, seeded so the slice is identical on every re-run and on
anyone else's machine.

    python eval/competitors/stratified_slice.py --data s --n 40            # print the qid list
    python eval/competitors/stratified_slice.py --data s --n 40 --args     # print as bench.py --qid flags

Abstention items (qid suffix `_abs`) are treated as their own stratum: LongMemEval scores them with a
different judge prompt, so they need proportional representation like any other category.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from eval.longmemeval import load_data  # noqa: E402


def stratum(item: dict) -> str:
    qid = str(item.get("question_id", ""))
    if qid.endswith("_abs"):
        return "abstention"
    return str(item.get("question_type", "?"))


def pick(items: list[dict], n: int, seed: int) -> list[str]:
    buckets: dict[str, list[str]] = {}
    for it in items:
        buckets.setdefault(stratum(it), []).append(str(it["question_id"]))
    for qids in buckets.values():
        qids.sort()  # file order is not guaranteed stable across dataset revisions; qid sort is

    total = sum(len(v) for v in buckets.values())
    rng = random.Random(seed)

    # Largest-remainder apportionment: floor everyone, then hand out what's left to the biggest
    # fractional parts. Keeps the slice's category mix as close to the full benchmark as N allows.
    exact = {k: len(v) * n / total for k, v in buckets.items()}
    take = {k: int(v) for k, v in exact.items()}
    for k in sorted(buckets, key=lambda k: (-(exact[k] - take[k]), k)):
        if sum(take.values()) >= n:
            break
        take[k] += 1

    out: list[str] = []
    for k in sorted(buckets):
        out.extend(rng.sample(buckets[k], min(take[k], len(buckets[k]))))
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="s")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20260817)
    ap.add_argument("--args", action="store_true", help="print as `--qid X --qid Y ...` for bench.py")
    a = ap.parse_args()

    items = load_data(a.data)
    qids = pick(items, a.n, a.seed)
    by = {q: stratum(i) for i in items for q in [str(i["question_id"])] if q in set(qids)}

    if a.args:
        print(" ".join(f"--qid {q}" for q in qids))
        return
    counts: dict[str, int] = {}
    for q in qids:
        counts[by[q]] = counts.get(by[q], 0) + 1
    print(json.dumps({"n": len(qids), "seed": a.seed, "by_category": counts, "qids": qids}, indent=2))


if __name__ == "__main__":
    main()
