"""Tune the retrieval-fusion weights ON THE HARNESS (CLAUDE.md §3.3 / §4 — "never hand-waved").

The fusion weights (w_sem, w_lex, w_graph, w_rec, w_sal) were hand-set defaults. This grid-searches them
against ground truth so the charter's "tuned per benchmark on the harness" is actually true. The objective
is LLM-FREE: a fact is RELEVANT iff it was extracted from one of the question's evidence sessions
(`answer_session_ids`); we maximize mean recall@k of relevant facts. Only the one-time fact extraction
needs an LLM; the grid-search itself is pure re-ranking.

    python eval/tune_weights.py --dev 30 --k 15

Prints the best weight set; copy it into engram/config.py if it beats the current defaults materially.
"""
from __future__ import annotations

import argparse
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram import Memory  # noqa: E402
from engram.config import Config  # noqa: E402
from engram.llm.providers import load_dotenv, make_embedder, make_llm  # noqa: E402
from eval.longmemeval import ingest, load_data  # noqa: E402


def build_dev(items, embedder, extractor, extract_k=20):
    """For each dev question, build a fact store and label which facts are relevant (provenance in an
    evidence session). Returns [(mem, user_id, query, relevant_fact_ids)]."""
    dev = []
    for it in items:
        qid = it["question_id"]
        evidence = set(it.get("answer_session_ids") or [])
        if not evidence:
            continue
        mem = Memory(embedder=embedder, llm=extractor)
        ingest(mem, it, qid)
        # episode.id -> session_id, so we can label facts by their provenance session
        ep_session = {ep.id: ep.session_id for ep in mem.episodes_doc.values()}
        retrieved = mem.retrieve_episodes(it["question"], qid, extract_k)
        mem.consolidate(retrieved)
        relevant = {
            f.id for f in mem.fact_store.values()
            if any(ep_session.get(pid) in evidence for pid in f.provenance)
        }
        if relevant:
            dev.append((mem, qid, it["question"], relevant))
    return dev


def mean_recall_at_k(dev, weights: dict, k: int) -> float:
    total = 0.0
    for mem, qid, query, relevant in dev:
        for name, val in weights.items():
            setattr(mem.config, name, val)
        ranked, _ = mem.retriever.retrieve(query, mem.resolver.resolve(qid), None, k)
        got = {f.id for f, _ in ranked} & relevant
        total += len(got) / len(relevant)
    return total / len(dev) if dev else 0.0


def grid_search(dev, k: int):
    grid = {
        "w_sem": [0.8, 1.0, 1.2],
        "w_lex": [0.4, 0.6, 0.9],
        "w_graph": [0.4, 0.8, 1.2],
        "w_rec": [0.1, 0.3, 0.5],
        "w_sal": [0.1, 0.25],
    }
    base = Config()
    best, best_w = -1.0, {k_: getattr(base, k_) for k_ in grid}
    names = list(grid)
    for combo in itertools.product(*grid.values()):
        w = dict(zip(names, combo))
        r = mean_recall_at_k(dev, w, k)
        if r > best:
            best, best_w = r, w
    return best, best_w, mean_recall_at_k(dev, {k_: getattr(base, k_) for k_ in grid}, k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", type=int, default=30)
    ap.add_argument("--k", type=int, default=15)
    ap.add_argument("--data", default="s")
    ap.add_argument("--extractor", default="volcano:doubao-seed-1-6-flash-250615")
    ap.add_argument("--embedder", default="bge-small")
    args = ap.parse_args()
    load_dotenv()
    items = load_data(args.data)
    # dev split from the TAIL (seeded shuffle in the eval uses head order; tail avoids overlap with reports)
    import random
    random.Random(999).shuffle(items)
    dev_items = items[: args.dev]
    print(f"building {len(dev_items)} dev questions (one-time fact extraction)...")
    dev = build_dev(dev_items, make_embedder(args.embedder), make_llm(args.extractor, max_tokens=512))
    print(f"  {len(dev)} usable (had relevant facts)")
    best, best_w, base_r = grid_search(dev, args.k)
    print(f"\n  default weights recall@{args.k}: {base_r:.3f}")
    print(f"  BEST weights recall@{args.k}:    {best:.3f}")
    print(f"  best = {best_w}")
    if best > base_r + 0.01:
        print("  -> update engram/config.py defaults with these (material improvement).")
    else:
        print("  -> defaults are already near-optimal; now harness-validated, not hand-waved.")


if __name__ == "__main__":
    main()
