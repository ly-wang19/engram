"""Where is the evidence lost — in the retriever, or after it?

For each question, embed every haystack session, rank them against the question, and ask whether the
gold `answer_session_ids` are in the top-k. This isolates one link in the chain: if the evidence session
IS in the retriever's top-k on questions we get wrong, the retriever found it and something downstream
(fact extraction, session summarization, context packing) dropped it. If it is NOT, the retriever is the
problem and no amount of downstream work helps.

The distinction decides where the remaining points are, and it costs nothing but CPU — no LLM, no judge.

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python eval/competitors/recall_audit.py --data s \
        --qids results/abstain_on_answerable_qids.json --k 8 --k 15 --k 28

Compare the failing set against a random control set of the same size (`--control N`), otherwise a high
recall number means nothing — it might be high everywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "2")  # this runs alongside benchmark jobs; do not hog cores
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engram.llm.providers import make_embedder  # noqa: E402
from eval.longmemeval import load_data, sessions_of  # noqa: E402


def rank_sessions(embedder, question: str, item: dict) -> list[str]:
    ids, texts = [], []
    for sid, turns in sessions_of(item):
        body = "\n".join(f"{t.get('role', 'user')}: {t.get('content', '')}" for t in turns
                         if t.get("content"))
        if body.strip():
            ids.append(sid)
            texts.append(body)
    if not texts:
        return []
    vecs = embedder.embed_batch(texts)
    qv = embedder.embed_batch([question])[0]

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    def norm(a):
        return sum(x * x for x in a) ** 0.5 or 1.0

    qn = norm(qv)
    scored = [(dot(qv, v) / (qn * norm(v)), sid) for v, sid in zip(vecs, ids)]
    scored.sort(reverse=True)
    return [sid for _, sid in scored]


def audit(embedder, items: list[dict], ks: list[int], label: str) -> dict:
    hits = {k: 0 for k in ks}
    ranks: list[int] = []
    n = 0
    for it in items:
        gold = set(it.get("answer_session_ids") or [])
        if not gold:
            continue
        order = rank_sessions(embedder, it["question"], it)
        if not order:
            continue
        n += 1
        best = min((order.index(g) for g in gold if g in order), default=10**6)
        ranks.append(best)
        for k in ks:
            if best < k:
                hits[k] += 1
    out = {"label": label, "n": n,
           "recall_at": {k: (hits[k] / n if n else 0.0) for k in ks},
           "median_rank": sorted(ranks)[len(ranks) // 2] if ranks else None}
    print(f"\n[{label}] n={n}  median gold-session rank={out['median_rank']}")
    for k in ks:
        print(f"   recall@{k:<3d} {100 * out['recall_at'][k]:5.1f}%   ({hits[k]}/{n})")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="s")
    ap.add_argument("--qids", default=None, help="JSON file with a 'qids' list (the failing set)")
    ap.add_argument("--k", type=int, action="append", default=None)
    ap.add_argument("--control", type=int, default=0, help="also audit N random other questions")
    ap.add_argument("--seed", type=int, default=20260817)
    ap.add_argument("--embedder", default="bge-small")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    ks = sorted(a.k or [8, 15, 28])

    items = {str(i["question_id"]): i for i in load_data(a.data)}
    embedder = make_embedder(a.embedder)

    results = []
    if a.qids:
        want = json.load(open(a.qids))["qids"]
        results.append(audit(embedder, [items[q] for q in want if q in items], ks, "failing set"))
        if a.control:
            rng = random.Random(a.seed)
            pool = [q for q in sorted(items) if q not in set(want)]
            results.append(audit(embedder, [items[q] for q in rng.sample(pool, a.control)], ks,
                                 "random control"))
    else:
        results.append(audit(embedder, list(items.values()), ks, "all"))

    if a.out:
        json.dump(results, open(a.out, "w"), indent=2)
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
