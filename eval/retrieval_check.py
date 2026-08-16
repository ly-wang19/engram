"""Did retrieval surface the evidence, or did reasoning fail on evidence it had?

`error_modes.py` shows that refusals and correct answers were given the same amount of context, which
rules out retrieval running dry but not retrieval returning the *wrong* sessions. Those two call for
opposite fixes — better recall versus better use of what was recalled — so the distinction has to be
settled before designing anything.

LongMemEval labels which sessions contain the answer (`answer_session_ids`), so this can be checked
directly. And checked **without spending anything**: session retrieval is driven by the embedder and
BM25 over raw text, not by the extractor LLM, so the whole thing runs on the local bge-small model.

    python3 eval/retrieval_check.py --failures-from results/longmemeval_s_engram_lean_v2_final.jsonl \
        --system engram_lean --out results/retrieval_check_failures.jsonl

A hit means the answer-bearing session was inside the retrieved slice. If refusals mostly hit, the
evidence was there and something after retrieval failed; if they mostly miss, retrieval is the target.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.compare import load  # noqa: E402
from eval.error_modes import classify  # noqa: E402

DEFAULT_DATASET = os.path.expanduser(
    "~/.cache/huggingface/hub/datasets--xiaowu0162--longmemeval/snapshots/"
    "2ec2a557f339b6c0369619b1ed5793734cc87533/longmemeval_s"
)


def failure_modes(log_path: str, system: str) -> dict[str, str]:
    """qid -> failure mode, for the questions this run got wrong."""
    out = {}
    for qid, entry in load(log_path).items():
        result = entry.get(system)
        if not result or result.get("err") or result.get("ok"):
            continue
        out[qid] = classify(result.get("pred") or "", result.get("gold") or "")
    return out


def check_item(item: dict, embedder, k_sessions: int) -> dict:
    """Retrieve for one question and report whether the answer's session came back.

    Ingests only this item's haystack, exactly as the benchmark's per-question setup does, so the
    retrieval being measured is the retrieval the run actually performed.
    """
    from engram.memory import Memory
    from engram.util import DAY, now

    mem = Memory(embedder=embedder)
    base = now() - len(item["haystack_sessions"]) * DAY
    session_of_episode = {}
    for index, (session_id, session) in enumerate(
        zip(item["haystack_session_ids"], item["haystack_sessions"])
    ):
        text = "\n".join(
            f"{turn.get('role', 'user')}: {turn.get('content', '')}" for turn in session
        )
        episode = mem.add(text, user_id="u", session_id=session_id, event_time=base + index * DAY)
        session_of_episode[episode.id] = session_id

    retrieved = mem.retrieve_episodes(item["question"], "u", k=k_sessions)
    ordered = [session_of_episode.get(ep.id, ep.session_id) for ep in retrieved]
    wanted = set(item.get("answer_session_ids") or [])
    # Rank, not just membership. The read path shows only the top few sessions in FULL detail and the
    # rest as summaries, so "somewhere in the top 15" and "shown as evidence the answerer can read" are
    # different claims — and they point at different layers to fix.
    rank = next((i + 1 for i, sid in enumerate(ordered) if sid in wanted), None)
    return {
        "qid": item["question_id"],
        "cat": item.get("question_type"),
        "answer_sessions": len(wanted),
        "haystack_sessions": len(item["haystack_session_ids"]),
        "hit": rank is not None,
        "rank": rank,
        "retrieved": len(ordered),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Was the answer's session retrieved at all?")
    ap.add_argument("--failures-from", required=True, help="a bench log; its wrong answers are checked")
    ap.add_argument("--system", default="engram_lean")
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--k-sessions", type=int, default=15, help="retrieved slice width to test")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    modes = failure_modes(args.failures_from, args.system)
    if not modes:
        print("no failures in that log")
        return 1

    with open(args.dataset, encoding="utf-8") as fh:
        dataset = {item["question_id"]: item for item in json.load(fh)}

    targets = [qid for qid in modes if qid in dataset]
    if args.limit:
        targets = targets[: args.limit]
    print(f"checking {len(targets)} failed questions (LLM-free; local embedder only)\n")

    from engram.llm.providers import make_embedder

    embedder = make_embedder("bge-small")

    rows = []
    started = time.time()
    for index, qid in enumerate(targets, start=1):
        row = check_item(dataset[qid], embedder, args.k_sessions)
        row["mode"] = modes[qid]
        rows.append(row)
        if index % 10 == 0 or index == len(targets):
            elapsed = time.time() - started
            print(f"  {index}/{len(targets)}  ({elapsed:.0f}s)", flush=True)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\n{'failure mode':<16}{'checked':>9}{'answer session retrieved':>26}")
    print("-" * 51)
    for mode in ("abstained", "numeric", "wrong_value"):
        subset = [r for r in rows if r["mode"] == mode]
        if not subset:
            continue
        hits = sum(1 for r in subset if r["hit"])
        print(f"{mode:<16}{len(subset):>9}{f'{hits}/{len(subset)}  ({hits/len(subset):.0%})':>26}")
    total_hits = sum(1 for r in rows if r["hit"])
    print(f"{'ALL':<16}{len(rows):>9}{f'{total_hits}/{len(rows)}  ({total_hits/len(rows):.0%})':>26}")

    # Where in the ranking it landed decides which layer is at fault: inside the full-detail window means
    # the answerer read it and still failed; outside means context assembly showed only a summary.
    ranks = [r["rank"] for r in rows if r["rank"]]
    if ranks:
        print("\nrank of the answer session within the retrieved slice:")
        for cut in (1, 2, 3, 5, 10, 15):
            within = sum(1 for r in ranks if r <= cut)
            print(f"  top-{cut:<3} {within:>4}/{len(rows)}  ({within/len(rows):.0%})")
        print(
            "\n  The run under analysis rendered its top 2 sessions in full and the rest as summaries,\n"
            "  so top-2 is the share where the answerer had the raw evidence in front of it."
        )
    print(
        "\nA high hit rate means the evidence was in the retrieved slice and the failure happened after\n"
        "retrieval — so recall expansion would buy nothing. A low one makes retrieval the target."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
