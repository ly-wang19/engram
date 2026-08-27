"""Per-feature read-path ablation on the real benchmark, measured WITHOUT an answerer or a judge.

Why not just run eval/bench.py --ablate per feature: the QA score is dominated by answerer variance
(run-to-run swings of ~±6-10 questions per 500 on the standard rig), which is larger than the effect
most single features have. Ablating 25+ flags through the QA path would therefore mostly measure the
answerer's mood, at 3x the cost.

So this measures the thing the read-path features actually control: **does the gold answer survive into
the retrieved context?** The answerer can't answer what retrieval never handed it, so answer-in-context
is the necessary condition these features are responsible for — and it needs no answerer and no judge,
which is both cheaper and far less noisy. (It is a necessary, not sufficient, condition: a feature can
put the answer in context and the answerer can still miss it. Promote anything interesting here to a
real eval/bench.py run before believing a headline delta.)

    python eval/ablate_readpath.py --limit 60 --extractor volcano:doubao-seed-1-6-flash-250615 \
        --out results/readpath_ablation.jsonl

Each feature is run as "baseline minus that one feature", so the delta is that feature's marginal
contribution on top of everything else — not its value in isolation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram import Memory  # noqa: E402
from engram.llm.providers import load_dotenv, make_embedder, make_llm  # noqa: E402
from eval.bench import engram_config  # noqa: E402
from eval.longmemeval import ingest, load_data  # noqa: E402

# Every read-path flag that engram_config() can switch off, grouped so the report reads as themes
# rather than an undifferentiated list of 25 booleans.
FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "graph / multi-hop": (
        "graph_proximity", "graph_relation_awareness", "graph_path_reinforcement",
        "graph_self_anchor", "graph_entity_alias_anchor", "graph_negative_constraints",
        "planner_location_chains", "planner_project_chains", "planner_llm_decomposition",
    ),
    "evidence planning": ("evidence_planner", "evidence_budgeting"),
    "temporal / chains": (
        "chain_evidence", "temporal_history_queries",
        "provenance_chunk_promotion", "provenance_evidence",
    ),
    "preference extraction": (
        "explicit_preference_extraction", "preference_object_filter",
        "preference_object_normalization", "preference_reversal_extraction",
    ),
    "aggregation": (
        "numeric_aggregation_candidates", "aggregation_recall_expansion",
        "aggregation_constraint_filter",
    ),
    "derived layers": ("summary_fallback", "procedural_memory", "procedural_extraction"),
}
ALL_FEATURES = tuple(f for group in FEATURE_GROUPS.values() for f in group)


def _norm(s: str) -> str:
    """Loose match: the answerer sees the context, not an exact-match grader, so casing/punctuation and
    inner whitespace must not decide whether the answer 'was there'."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def answer_in_context(context: str, gold: str) -> bool:
    """Whether the gold answer is recoverable from the retrieved context.

    Short golds ('Nike', '55-inch') are checked whole. Long golds are sentences, and demanding a verbatim
    sentence match would under-count contexts that clearly carry the fact in different words; those are
    scored on content-token coverage instead (>=80% of tokens >3 chars present).
    """
    ctx, g = _norm(context), _norm(gold)
    if not g:
        return False
    if g in ctx:
        return True
    toks = [t for t in g.split() if len(t) > 3]
    if not toks:
        return False
    hit = sum(1 for t in toks if t in ctx)
    return hit / len(toks) >= 0.8


def context_for_item(item: dict, embedder, extractor, ablations: tuple[str, ...],
                     topk: int, chunks: int, extract_k: int) -> tuple[str, int]:
    qid, q = item["question_id"], item["question"]
    mem = Memory(config=engram_config(ablations=ablations), embedder=embedder, llm=extractor)
    ingest(mem, item, qid)
    if extract_k > 0:
        mem.engine.consolidate(mem.retrieve_episodes(q, qid, extract_k))
    else:
        mem.consolidate()
    ctx = mem.context_for(q, user_id=qid, top_k=topk, k_chunks=chunks)
    return ctx, len(ctx) // 4  # rough token estimate, only used for a relative cost column


def run_variant(items, embedder, extractor, ablations, args, label) -> dict:
    hits = 0
    tokens = 0
    errors = 0
    t0 = time.time()

    def one(item):
        try:
            ctx, tok = context_for_item(item, embedder, extractor, ablations,
                                        args.topk, args.chunks, args.extract_k)
            return answer_in_context(ctx, item.get("answer", "")), tok, None
        except Exception as exc:  # noqa: BLE001 — one bad item must not kill the sweep
            return False, 0, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for ok, tok, err in pool.map(one, items):
            hits += int(ok)
            tokens += tok
            errors += int(err is not None)
    n = len(items)
    return {"label": label, "ablated": list(ablations), "answer_in_context": hits,
            "n": n, "rate": hits / n if n else 0.0,
            "avg_tokens": tokens // n if n else 0, "errors": errors,
            "elapsed_s": round(time.time() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="s")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--extractor", default="volcano:doubao-seed-1-6-flash-250615")
    ap.add_argument("--embedder", default="bge-small")
    ap.add_argument("--topk", type=int, default=15)
    ap.add_argument("--chunks", type=int, default=2)
    ap.add_argument("--extract-k", type=int, default=8)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--features", default="", help="comma-separated subset (default: all)")
    ap.add_argument("--repeat-baseline", type=int, default=2,
                    help="re-run the baseline N times to measure this rig's own noise floor")
    ap.add_argument("--out", default="results/readpath_ablation.jsonl")
    args = ap.parse_args()

    load_dotenv()
    items = load_data(args.data)[:args.limit]
    items = [it for it in items if it.get("answer")]
    embedder = make_embedder(args.embedder)
    extractor = make_llm(args.extractor)
    features = tuple(f.strip() for f in args.features.split(",") if f.strip()) or ALL_FEATURES

    print(f"READ-PATH ABLATION | {len(items)} items | {len(features)} features")
    print(f"  metric=answer_in_context (no answerer, no judge)  extractor={args.extractor}\n")

    rows = []
    # Baseline, repeated: the spread across identical runs IS the noise floor. A feature delta smaller
    # than this band is not evidence of anything.
    baselines = []
    for i in range(max(1, args.repeat_baseline)):
        b = run_variant(items, embedder, extractor, (), args, f"baseline#{i+1}")
        baselines.append(b)
        rows.append(b)
        print(f"  baseline#{i+1}: {b['answer_in_context']}/{b['n']} = {b['rate']*100:.1f}%"
              f"  ({b['elapsed_s']}s)")
    base_rate = sum(b["rate"] for b in baselines) / len(baselines)
    noise = (max(b["rate"] for b in baselines) - min(b["rate"] for b in baselines)) if len(baselines) > 1 else 0.0
    print(f"  baseline mean {base_rate*100:.1f}%   noise band ±{noise*100:.1f} pp\n")

    for f in features:
        r = run_variant(items, embedder, extractor, (f,), args, f"-{f}")
        r["delta_pp"] = round((base_rate - r["rate"]) * 100, 1)  # positive => feature HELPS
        rows.append(r)
        verdict = "helps" if r["delta_pp"] > noise * 100 else ("hurts" if r["delta_pp"] < -noise * 100 else "noise")
        print(f"  -{f:<34} {r['rate']*100:5.1f}%   delta {r['delta_pp']:+5.1f} pp   [{verdict}]")

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as fh:
            fh.write(json.dumps({"kind": "readpath_ablation_meta", "items": len(items),
                                 "baseline_rate": base_rate, "noise_band_pp": noise * 100,
                                 "extractor": args.extractor, "embedder": args.embedder,
                                 "topk": args.topk, "chunks": args.chunks,
                                 "extract_k": args.extract_k}) + "\n")
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
