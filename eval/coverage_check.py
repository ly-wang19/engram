"""Does widening the detail window for counting questions actually cover the evidence?

`retrieval_diagnosis.md` established that counting failures are a coverage problem: the answer spans
several sessions, retrieval finds them, and the detail window renders about half. `aggregation_chunk_cap`
is the proposed fix. This measures whether it does what it claims — before anyone pays for a run.

The measurement has to mirror the real selection loop, not a simplification of it. `lean_context` does
not take the top-N sessions for the main query: it retrieves per subquery and interleaves by rank
(`memory.py`, the `detail_eps` loop). Testing top-N instead would measure a mechanism the code does not
have, and could report a gain that evaporates in production.

LLM-free — retrieval runs on the local embedder, and the benchmark labels which sessions hold the answer.

    python3 eval/coverage_check.py --failures-from results/longmemeval_s_engram_lean_v2_final.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engram.retrieve.evidence import plan_evidence  # noqa: E402
from eval.error_modes import classify  # noqa: E402
from eval.retrieval_check import DEFAULT_DATASET, failure_modes  # noqa: E402

__all__ = ["select_detail_sessions", "coverage_for"]


def select_detail_sessions(mem, session_of_episode: dict, query: str, need, n_chunks: int) -> list[str]:
    """The session ids `lean_context` would render in full, for this query and budget.

    Mirrors the round-robin in memory.py: each subquery contributes its rank-1 session first, then every
    subquery's rank-2, and so on until the budget is spent. That ordering is the whole point — it spreads
    the window across the decomposed angles instead of spending it all on the main query's top hits.
    """
    if n_chunks <= 0:
        return []
    detail_queries = list(need.subqueries) + [query] if need.subqueries else [query]
    per_query = [mem.retrieve_episodes(q, "u", max(n_chunks, 1)) for q in detail_queries]

    seen: set[str] = set()
    chosen: list[str] = []
    for rank in range(max((len(eps) for eps in per_query), default=0)):
        for eps in per_query:
            if rank >= len(eps):
                continue
            episode = eps[rank]
            if episode.id in seen:
                continue
            seen.add(episode.id)
            chosen.append(session_of_episode.get(episode.id, episode.session_id))
            if len(chosen) >= n_chunks:
                return chosen
    return chosen


def coverage_for(item: dict, embedder, cap: int) -> dict:
    from engram.memory import Memory
    from engram.util import DAY, now

    mem = Memory(embedder=embedder)
    base = now() - len(item["haystack_sessions"]) * DAY
    session_of_episode = {}
    for index, (session_id, session) in enumerate(
        zip(item["haystack_session_ids"], item["haystack_sessions"])
    ):
        text = "\n".join(f"{t.get('role', 'user')}: {t.get('content', '')}" for t in session)
        episode = mem.add(text, user_id="u", session_id=session_id, event_time=base + index * DAY)
        session_of_episode[episode.id] = session_id

    wanted = set(item.get("answer_session_ids") or [])
    question = item["question"]

    # The run under analysis passed --chunks 2 on the CLI, and lean_context takes max(cli, planner), so
    # the baseline here is 2 rather than the planner's 1. Comparing against the planner alone would
    # flatter the change by pretending the baseline was worse than it was.
    before_need = plan_evidence(question, aggregation_chunk_cap=0)
    after_need = plan_evidence(question, aggregation_chunk_cap=cap)
    before_chunks = max(2, before_need.n_chunks)
    after_chunks = max(2, after_need.n_chunks)

    before = select_detail_sessions(mem, session_of_episode, question, before_need, before_chunks)
    after = select_detail_sessions(mem, session_of_episode, question, after_need, after_chunks)

    return {
        "qid": item["question_id"],
        "answer_sessions": len(wanted),
        "chunks_before": before_chunks,
        "chunks_after": after_chunks,
        "covered_before": len(wanted & set(before)),
        "covered_after": len(wanted & set(after)),
        "complete_before": wanted.issubset(set(before)),
        "complete_after": wanted.issubset(set(after)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Does the wider detail window cover the answer sessions?")
    ap.add_argument("--failures-from", required=True)
    ap.add_argument("--system", default="engram_lean")
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--cap", type=int, default=5, help="aggregation_chunk_cap to test")
    ap.add_argument("--mode", default="numeric", help="failure mode to check")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    modes = failure_modes(args.failures_from, args.system)
    with open(args.dataset, encoding="utf-8") as fh:
        dataset = {item["question_id"]: item for item in json.load(fh)}

    targets = [
        qid for qid, mode in modes.items()
        if mode == args.mode and qid in dataset and len(dataset[qid].get("answer_session_ids") or []) > 1
    ]
    if not targets:
        print("no multi-session failures of that mode")
        return 1
    print(f"checking {len(targets)} multi-session {args.mode} failures at cap={args.cap} (LLM-free)\n")

    from engram.llm.providers import make_embedder

    embedder = make_embedder("bge-small")

    rows = []
    started = time.time()
    for index, qid in enumerate(targets, start=1):
        rows.append(coverage_for(dataset[qid], embedder, args.cap))
        if index % 10 == 0 or index == len(targets):
            print(f"  {index}/{len(targets)}  ({time.time() - started:.0f}s)", flush=True)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def ratio(row, key):
        return row[key] / row["answer_sessions"] if row["answer_sessions"] else 0.0

    before_cov = statistics.fmean(ratio(r, "covered_before") for r in rows)
    after_cov = statistics.fmean(ratio(r, "covered_after") for r in rows)
    before_full = sum(1 for r in rows if r["complete_before"])
    after_full = sum(1 for r in rows if r["complete_after"])
    chunks_before = statistics.fmean(r["chunks_before"] for r in rows)
    chunks_after = statistics.fmean(r["chunks_after"] for r in rows)

    print(f"\n{'':<26}{'before':>10}{'after':>10}")
    print("-" * 46)
    print(f"{'mean answer-session coverage':<26}{before_cov:>9.0%}{after_cov:>10.0%}")
    print(f"{'fully covered':<26}{f'{before_full}/{len(rows)}':>10}{f'{after_full}/{len(rows)}':>10}")
    print(f"{'mean full sessions rendered':<26}{chunks_before:>10.1f}{chunks_after:>10.1f}")

    gained = after_full - before_full
    print(
        f"\n{gained} more questions now have every answer session in view "
        f"(+{100.0 * gained / 500:.1f} points at best, if each converts to a correct answer)."
    )
    print(
        "That ceiling assumes a conversion of 1, which the same log refutes: questions already failed\n"
        "with their evidence in full detail. The measured gain needs a keyed run; this only establishes\n"
        "that the mechanism does what it is supposed to do, and at what cost in rendered sessions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
