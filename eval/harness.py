"""Reproducible eval harness.

    python eval/harness.py

Runs Engram over each item's history and scores per category, reporting the triple the charter mandates
(accuracy + context tokens + latency). A "naive recall" reference is included as a stand-in for the
full-context baseline: it can recall any fact present in the raw history but cannot scope to a point in
time, cannot tell which of two contradictory facts is current, and cannot abstain -- which is exactly
the capability surface a memory system has to add. (M1 swaps in real LOCOMO/LongMemEval + an LLM judge
+ a true full-context+LLM baseline behind this same harness.)
"""
from __future__ import annotations

import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram import Memory  # noqa: E402
from engram.util import DAY, stems  # noqa: E402
from eval.synthetic import ITEMS, EvalItem  # noqa: E402

BASE = 1_700_000_000.0


def _norm(s: str) -> str:
    return " ".join(stems(s))


def _contains(answer: str, gold: str) -> bool:
    return _norm(gold) in _norm(answer)


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[idx]


def run_engram(item: EvalItem) -> tuple[bool, int, float]:
    mem = Memory()
    for text, day in item.sessions:
        mem.add(text, user_id=item.id, event_time=BASE + day * DAY)
    mem.consolidate()
    when = None if item.as_of_day is None else BASE + item.as_of_day * DAY

    t0 = time.perf_counter()
    res = mem.search(item.question, user_id=item.id, as_of=when)
    latency_ms = (time.perf_counter() - t0) * 1000.0

    if item.answer is None:
        correct = res.abstained
    else:
        correct = (not res.abstained) and _contains(res.answer(), item.answer)
    context_tokens = len(stems(" ".join(f.text for f in res.facts)))
    return correct, context_tokens, latency_ms


def run_naive_recall(item: EvalItem) -> tuple[bool, int]:
    history_text = " ".join(t for t, _ in item.sessions)
    tokens = len(stems(history_text))
    if item.answer is None:
        return False, tokens  # naive reader cannot abstain
    if item.as_of_day is not None:
        return False, tokens  # naive reader has no temporal scoping
    return _contains(history_text, item.answer), tokens


def main() -> None:
    cats = sorted({it.category for it in ITEMS})
    eng_hits: dict[str, list[bool]] = defaultdict(list)
    base_hits: dict[str, list[bool]] = defaultdict(list)
    eng_tokens: list[int] = []
    base_tokens: list[int] = []
    latencies: list[float] = []

    print(f"Engram eval harness -- {len(ITEMS)} items, offline (hashing embedder + rule extractor)\n")
    print(f"  {'id':5} {'category':16} {'engram':7} {'naive':6}  question")
    print("  " + "-" * 78)
    for it in ITEMS:
        e_ok, e_tok, e_lat = run_engram(it)
        b_ok, b_tok = run_naive_recall(it)
        eng_hits[it.category].append(e_ok)
        base_hits[it.category].append(b_ok)
        eng_tokens.append(e_tok)
        base_tokens.append(b_tok)
        latencies.append(e_lat)
        print(f"  {it.id:5} {it.category:16} {'PASS' if e_ok else 'FAIL':7} {'ok' if b_ok else '--':6}  {it.question}")

    def acc(hits: dict[str, list[bool]]) -> float:
        flat = [h for v in hits.values() for h in v]
        return 100.0 * sum(flat) / len(flat) if flat else 0.0

    print("\n  Per-category accuracy (engram / naive-recall):")
    for c in cats:
        e = 100.0 * sum(eng_hits[c]) / len(eng_hits[c])
        b = 100.0 * sum(base_hits[c]) / len(base_hits[c])
        print(f"    {c:16} {e:6.1f}% / {b:5.1f}%")

    print("\n  Overall:")
    print(f"    accuracy        engram {acc(eng_hits):5.1f}%   naive-recall {acc(base_hits):5.1f}%")
    print(f"    context tokens  engram {sum(eng_tokens)/len(eng_tokens):5.1f}    "
          f"naive-recall {sum(base_tokens)/len(base_tokens):5.1f}   "
          f"({(sum(base_tokens)/max(1,sum(eng_tokens))):.1f}x leaner)")
    print(f"    latency (ms)    p50 {_percentile(latencies,50):.2f}   p95 {_percentile(latencies,95):.2f}")
    print("\n  Note: 'naive-recall' is a recall ceiling; it cannot abstain, scope by time, or resolve")
    print("  knowledge updates. Those columns are where a memory system earns its keep.")


if __name__ == "__main__":
    main()
