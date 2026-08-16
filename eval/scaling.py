"""Read-path scaling measurement (charter Bet E).

The charter claims linear scaling to 10M+ tokens, and the coding conventions say performance claims
come from the harness, not intuition. This script is that harness for the *read* path: it grows the
fact store and measures what `HybridRetriever.retrieve()` actually costs per query.

It is deliberately offline and deterministic (hashing embedder, synthetic facts), so it measures the
*shape* of the cost curve, not absolute production latency. The shape is the point: a retriever that
scores every live fact per query is O(n), and no vector backend fixes that on its own.

    python3 eval/scaling.py                    # default sizes
    python3 eval/scaling.py --sizes 100,1000,5000 --trials 20
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engram.config import Config  # noqa: E402
from engram.embed.hashing import HashingEmbedder  # noqa: E402
from engram.retrieve.hybrid import HybridRetriever  # noqa: E402
from engram.store.base import VectorStore  # noqa: E402
from engram.store.indexed import IndexedVectorStore  # noqa: E402
from engram.store.memory_store import InMemoryGraphStore, InMemoryVectorStore  # noqa: E402
from engram.types import Fact  # noqa: E402
from engram.util import now  # noqa: E402

# A small vocabulary reused across facts so lexical scoring has real term statistics rather than
# every document being disjoint (which would make BM25 trivially cheap and hide the scan cost).
SUBJECTS = ["alice", "bob", "carol", "dave", "erin", "frank", "grace", "heidi"]
PREDICATES = ["works_at", "lives_in", "prefers", "visited", "studied", "owns", "avoids", "plans"]
OBJECTS = [
    "acme corp", "berlin", "oat milk", "kyoto", "linear algebra", "a road bike",
    "crowded cafes", "a trip to lisbon", "the night shift", "sourdough baking",
]


def build_facts(n: int, embedder: HashingEmbedder, user_id: str = "u1") -> list[Fact]:
    """Deterministic synthetic facts spread over a year of valid-time."""
    t = now()
    facts: list[Fact] = []
    for i in range(n):
        subj = SUBJECTS[i % len(SUBJECTS)]
        pred = PREDICATES[(i // len(SUBJECTS)) % len(PREDICATES)]
        obj = OBJECTS[(i // (len(SUBJECTS) * len(PREDICATES))) % len(OBJECTS)]
        text = f"{subj} {pred.replace('_', ' ')} {obj} (record {i})"
        f = Fact(
            user_id=user_id,
            subject=subj,
            predicate=pred,
            object=f"{obj} {i}",
            text=text,
            valid_at=t - (i % 365) * 86400.0,
            embedding=embedder.embed(text),
        )
        facts.append(f)
    return facts


def measure(
    n: int,
    trials: int,
    queries: list[str],
    *,
    bounded: bool = False,
    pool: int = 400,
    vector_channel: bool = True,
) -> dict:
    embedder = HashingEmbedder()
    store: VectorStore = InMemoryVectorStore()
    if bounded:
        store = IndexedVectorStore(store)
    graph = InMemoryGraphStore()
    for f in build_facts(n, embedder):
        store.upsert(f.id, f.embedding or [], f)

    config = Config(
        bounded_candidates=bounded, candidate_pool=pool, candidate_vector_channel=vector_channel
    )
    retriever = HybridRetriever(store, graph, embedder, config)

    # warm up so first-call import/alloc costs do not land in the sample
    retriever.retrieve(queries[0], "u1")

    samples: list[float] = []
    for i in range(trials):
        q = queries[i % len(queries)]
        t0 = time.perf_counter()
        retriever.retrieve(q, "u1")
        samples.append((time.perf_counter() - t0) * 1000.0)

    samples.sort()
    return {
        "n": n,
        "p50_ms": statistics.median(samples),
        "p95_ms": samples[min(len(samples) - 1, int(len(samples) * 0.95))],
        "mean_ms": statistics.fmean(samples),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure read-path cost vs. fact-store size.")
    ap.add_argument("--sizes", default="100,500,2000,10000", help="comma-separated fact counts")
    ap.add_argument("--trials", type=int, default=15, help="queries timed per size")
    ap.add_argument("--pool", type=int, default=400, help="candidate pool for the bounded path")
    args = ap.parse_args()

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    queries = [
        "where does alice work",
        "what does bob prefer to drink",
        "which city did carol visit last year",
        "what is dave studying",
    ]

    variants = [
        ("full scan", [measure(n, args.trials, queries) for n in sizes]),
        (
            "bounded +vec",
            [measure(n, args.trials, queries, bounded=True, pool=args.pool) for n in sizes],
        ),
        (
            "bounded -vec",
            [
                measure(n, args.trials, queries, bounded=True, pool=args.pool, vector_channel=False)
                for n in sizes
            ],
        ),
    ]

    print(f"candidate pool = {args.pool}\n")
    header = f"{'facts':>8}" + "".join(f"{name:>15}" for name, _ in variants)
    print(header)
    print("-" * len(header))
    for i, n in enumerate(sizes):
        row = f"{n:>8}" + "".join(f"{rows[i]['p50_ms']:>13.2f}ms" for _, rows in variants)
        print(row)

    print("\nms per 1k facts (constant = O(n), falling = sub-linear):")
    for name, rows in variants:
        cells = "".join(f"{r['p50_ms'] / (r['n'] / 1000.0):>10.2f}" for r in rows)
        print(f"  {name:>13}{cells}")

    print(f"\nstore grew {sizes[-1] / sizes[0]:.0f}x ({sizes[0]} -> {sizes[-1]} facts)")
    for name, rows in variants:
        growth = rows[-1]["p50_ms"] / rows[0]["p50_ms"] if rows[0]["p50_ms"] else float("nan")
        print(f"  {name:>13}: per-query cost grew {growth:>6.1f}x")
    print(
        "\n'+vec' asks the vector store for semantic candidates. Neither shipped backend has a real ANN\n"
        "index, so that call is itself a full scan — which is why bounding the lexical and fusion work\n"
        "alone does not pay off. '-vec' is what the read path costs once no channel scans the store."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
