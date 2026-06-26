"""Reproducible eval harness.

    python eval/harness.py
    python eval/harness.py --storage durable --out results/synthetic_durable.jsonl
    python eval/report.py results/synthetic_durable.jsonl

Runs Engram over each item's history and scores per category, reporting the triple the charter mandates
(accuracy + context tokens + latency). A "naive recall" reference is included as a stand-in for the
full-context baseline: it can recall any fact present in the raw history but cannot scope to a point in
time, cannot tell which of two contradictory facts is current, and cannot abstain -- which is exactly
the capability surface a memory system has to add. (M1 swaps in real LOCOMO/LongMemEval + an LLM judge
+ a true full-context+LLM baseline behind this same harness.)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import argparse
import json
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram import Config, Memory  # noqa: E402
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


def _memory_for(storage: str, data_path: str | None = None) -> Memory:
    if storage == "memory":
        return Memory()
    cfg = Config(storage="lancedb", data_path=data_path) if storage == "lancedb" else Config()
    return Memory(config=cfg)


def run_engram(item: EvalItem, storage: str = "memory") -> tuple[bool, int, float]:
    detail = run_engram_detail(item, storage=storage)
    return detail["ok"], detail["tokens"], detail["latency_ms"]


def run_engram_detail(item: EvalItem, storage: str = "memory") -> dict:
    tmp = tempfile.mkdtemp(prefix=f"engram_eval_{storage}_") if storage != "memory" else None
    mem = _memory_for(storage, os.path.join(tmp, "vectors") if tmp else None)
    for text, day in item.sessions:
        mem.add(text, user_id=item.id, event_time=BASE + day * DAY)
    mem.consolidate()
    if tmp is not None:
        path = os.path.join(tmp, "store")
        mem.save(path)
        mem = Memory.open(
            path,
            config=Config(storage="lancedb", data_path=os.path.join(tmp, "vectors"))
            if storage == "lancedb" else Config(),
        )
    when = None if item.as_of_day is None else BASE + item.as_of_day * DAY

    t0 = time.perf_counter()
    res = mem.search(item.question, user_id=item.id, as_of=when)
    latency_ms = (time.perf_counter() - t0) * 1000.0

    if item.answer is None:
        correct = res.abstained
    else:
        correct = (not res.abstained) and _contains(res.answer(), item.answer)
    context_tokens = len(stems(" ".join(f.text for f in res.facts)))
    if tmp is not None:
        shutil.rmtree(tmp, ignore_errors=True)
    return {
        "id": item.id,
        "category": item.category,
        "question": item.question,
        "gold": item.answer,
        "storage": storage,
        "ok": correct,
        "answer": res.answer(),
        "abstained": res.abstained,
        "tokens": context_tokens,
        "latency_ms": latency_ms,
        "fact_ids": [f.id for f in res.facts],
    }


def run_naive_recall(item: EvalItem) -> tuple[bool, int]:
    history_text = " ".join(t for t, _ in item.sessions)
    tokens = len(stems(history_text))
    if item.answer is None:
        return False, tokens  # naive reader cannot abstain
    if item.as_of_day is not None:
        return False, tokens  # naive reader has no temporal scoping
    return _contains(history_text, item.answer), tokens


def evaluate(storage: str = "memory") -> tuple[list[dict], dict]:
    cats = sorted({it.category for it in ITEMS})
    rows: list[dict] = []
    eng_hits: dict[str, list[bool]] = defaultdict(list)
    base_hits: dict[str, list[bool]] = defaultdict(list)
    eng_tokens: list[int] = []
    base_tokens: list[int] = []
    latencies: list[float] = []

    for it in ITEMS:
        e = run_engram_detail(it, storage=storage)
        b_ok, b_tok = run_naive_recall(it)
        row = {
            **e,
            "naive_ok": b_ok,
            "naive_tokens": b_tok,
        }
        rows.append(row)
        eng_hits[it.category].append(e["ok"])
        base_hits[it.category].append(b_ok)
        eng_tokens.append(e["tokens"])
        base_tokens.append(b_tok)
        latencies.append(e["latency_ms"])

    def acc(values: list[bool]) -> float:
        return 100.0 * sum(values) / len(values) if values else 0.0

    per_category = {
        c: {
            "engram_accuracy": acc(eng_hits[c]),
            "naive_accuracy": acc(base_hits[c]),
            "n": len(eng_hits[c]),
        }
        for c in cats
    }
    summary = {
        "type": "summary",
        "storage": storage,
        "n": len(rows),
        "accuracy": acc([r["ok"] for r in rows]),
        "naive_accuracy": acc([r["naive_ok"] for r in rows]),
        "avg_tokens": sum(eng_tokens) / len(eng_tokens) if eng_tokens else 0.0,
        "naive_avg_tokens": sum(base_tokens) / len(base_tokens) if base_tokens else 0.0,
        "token_ratio": sum(base_tokens) / max(1, sum(eng_tokens)),
        "latency_p50_ms": _percentile(latencies, 50),
        "latency_p95_ms": _percentile(latencies, 95),
        "per_category": per_category,
    }
    return rows, summary


def write_jsonl(path: str, rows: list[dict], summary: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        fh.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the built-in offline Engram synthetic eval.")
    ap.add_argument("--storage", choices=["memory", "durable", "lancedb"], default="memory",
                    help="memory uses RAM only; durable/lancedb save and reopen before search")
    ap.add_argument("--out", help="write per-item raw logs plus a final summary record as JSONL")
    args = ap.parse_args()

    rows, summary = evaluate(storage=args.storage)
    if args.out:
        write_jsonl(args.out, rows, summary)

    print(f"Engram eval harness -- {len(ITEMS)} items, offline (hashing embedder + rule extractor)")
    print(f"storage={args.storage} ({'save/reopen before search' if args.storage != 'memory' else 'RAM only'})\n")
    print(f"  {'id':5} {'category':16} {'engram':7} {'naive':6}  question")
    print("  " + "-" * 78)
    for row in rows:
        print(
            f"  {row['id']:5} {row['category']:16} "
            f"{'PASS' if row['ok'] else 'FAIL':7} {'ok' if row['naive_ok'] else '--':6}  {row['question']}"
        )

    print("\n  Per-category accuracy (engram / naive-recall):")
    for c, vals in summary["per_category"].items():
        print(f"    {c:16} {vals['engram_accuracy']:6.1f}% / {vals['naive_accuracy']:5.1f}%")

    print("\n  Overall:")
    print(f"    accuracy        engram {summary['accuracy']:5.1f}%   "
          f"naive-recall {summary['naive_accuracy']:5.1f}%")
    print(f"    context tokens  engram {summary['avg_tokens']:5.1f}    "
          f"naive-recall {summary['naive_avg_tokens']:5.1f}   "
          f"({summary['token_ratio']:.1f}x leaner)")
    print(f"    latency (ms)    p50 {summary['latency_p50_ms']:.2f}   p95 {summary['latency_p95_ms']:.2f}")
    if args.out:
        print(f"\n  Raw log written to {args.out}")
    print("\n  Note: 'naive-recall' is a recall ceiling; it cannot abstain, scope by time, or resolve")
    print("  knowledge updates. Those columns are where a memory system earns its keep.")


if __name__ == "__main__":
    main()
