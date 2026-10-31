"""Offline session-evidence coverage, NOT QA accuracy or final lean-context coverage.

    python eval/evidence_coverage.py --data s --limit 0 --out results/coverage.jsonl \
        --embedder bge-small

The experimental arm changes two things together: independent positive BM25 candidates
and no RRF vote for absent/zero lexical evidence. It does not isolate their contributions.
Both arms return the same k. No answer, gold ID, or benchmark category enters ranking.
BGE weights and named datasets must already be cached locally; no API keys or LLM calls.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict, defaultdict
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engram import Memory  # noqa: E402
from engram.embed.base import Embedder  # noqa: E402
from engram.retrieve.lexical import bm25_scores  # noqa: E402
from eval.longmemeval import ingest, parse_date  # noqa: E402

KS = (2, 4, 8, 28)
RRF_K = 60


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class CachedEmbedder(Embedder):
    """Content-addressed vectors only; cache files never contain input text."""

    def __init__(self, factory, identity: dict, directory: Path):
        self.factory, self.identity = factory, identity
        self.dim = identity["dim"]
        self.directory = Path(directory) / digest(identity)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.inner = None
        self.hot: OrderedDict[str, list[float]] = OrderedDict()
        self.hits = self.misses = 0

    def embed(self, text):
        return self.embed_batch([text])[0]

    def embed_batch(self, texts):
        keys = [digest([self.identity, text]) for text in texts]
        found, missing = {}, {}
        for key, text in zip(keys, texts):
            if key in found or key in missing:
                continue
            path = self.directory / (key + ".json")
            if key in self.hot:
                found[key] = self.hot[key]
                self.hits += 1
            elif path.exists():
                vec = json.loads(path.read_text())
                if len(vec) != self.dim or not all(isinstance(x, (float, int)) and math.isfinite(x) for x in vec):
                    raise ValueError(f"Invalid embedding cache entry: {path.name}")
                found[key] = vec
                self.hits += 1
            else:
                missing[key] = text
        if missing:
            if self.inner is None:
                self.inner = self.factory()
            vectors = self.inner.embed_batch(list(missing.values()))
            if len(vectors) != len(missing):
                raise ValueError("Embedder returned the wrong batch length")
            for key, vec in zip(missing, vectors):
                if len(vec) != self.dim or not all(math.isfinite(x) for x in vec):
                    raise ValueError("Embedder returned an invalid vector")
                found[key] = list(vec)
                atomic_json(self.directory / (key + ".json"), found[key])
                self.misses += 1
        for key, vec in found.items():
            self.hot[key] = vec
            self.hot.move_to_end(key)
        while len(self.hot) > 256:
            self.hot.popitem(last=False)
        return [found[key] for key in keys]


def model_spec(name: str, device: str):
    if name == "hashing":
        from engram.embed import HashingEmbedder

        return {"name": "hashing", "dim": 384, "implementation": inspect.getsource(HashingEmbedder)}, lambda: HashingEmbedder(384)
    from huggingface_hub import snapshot_download
    from engram.embed import SentenceTransformerEmbedder

    model = "BAAI/bge-small-en-v1.5"
    snapshot = Path(snapshot_download(model, local_files_only=True)).resolve()
    identity = {
        "name": model, "revision": snapshot.name, "dim": 384, "device": device,
        "normalize": True, "batch_size": 64,
        "sentence_transformers": importlib.metadata.version("sentence-transformers"),
        "transformers": importlib.metadata.version("transformers"),
        "torch": importlib.metadata.version("torch"),
        "config_sha256": file_hash(snapshot / "config.json"),
    }
    return identity, lambda: SentenceTransformerEmbedder(str(snapshot), device=device)


def load_dataset(source: str):
    if source in {"s", "m", "oracle"}:
        from huggingface_hub import hf_hub_download

        path = Path(hf_hub_download("xiaowu0162/longmemeval", "longmemeval_" + source,
                                   repo_type="dataset", local_files_only=True))
        origin = "hf://datasets/xiaowu0162/longmemeval/longmemeval_" + source
    else:
        path = Path(source).expanduser().resolve()
        origin = str(path)
    with path.open() as handle:
        items = json.load(handle)
    return items, {"source": origin, "resolved_path": str(path), "sha256": file_hash(path)}


def prepare_candidates(mem, query: str, user_id: str, as_of=None):
    """Gold-free shared work: one full-corpus lexical pass and one dense ordering."""
    user = mem.resolver.resolve(user_id)
    eligible = [e for e in mem.episodes_doc.values()
                if e.user_id == user and (as_of is None or e.event_time <= as_of)]
    dense = [e for _, e in mem.episodes_vec.search(
        mem.embedder.embed(query), len(eligible),
        where=lambda e: e.user_id == user and (as_of is None or e.event_time <= as_of))]
    scores = bm25_scores(query, [(e.id, e.content) for e in eligible])
    lexical = sorted((e for e in eligible if scores.get(e.id, 0) > 0),
                     key=lambda e: -scores[e.id])
    return dense, lexical


def independent_rrf(dense, lexical, k: int):
    """Fuse the original dense pool and independent positive lexical evidence only."""
    pool = max(k * 5, 25)
    scores, episodes = {}, {}
    for ranking in (dense[:pool], lexical):
        for rank, ep in enumerate(ranking, 1):
            episodes.setdefault(ep.id, ep)
            scores[ep.id] = scores.get(ep.id, 0.0) + 1.0 / (RRF_K + rank)
    # Stable insertion order resolves ties using dense first, then lexical order.
    ranked = sorted(episodes.values(), key=lambda e: -scores[e.id])
    return ranked[:k], ranked


def coverage(selected_ids, gold):
    gold = set(gold)
    if not gold:
        raise ValueError("Coverage requires at least one annotated gold session")
    hits = len(set(selected_ids) & gold)
    return {"any_hit": int(hits > 0), "all_hit": int(hits == len(gold)),
            "fractional": hits / len(gold), "gold_hits": hits}


def evaluate_item(item, embedder, as_of_mode="none"):
    qid = str(item["question_id"])
    gold = {str(g) for g in item.get("answer_session_ids", [])}
    row = {"qid": qid, "category": item.get("question_type", "unknown"), "gold_count": len(gold)}
    if qid.endswith("_abs") or not gold:
        row["status"] = "excluded_abs" if qid.endswith("_abs") else "excluded_no_gold"
        return row
    parsed = parse_date(item.get("question_date"))
    if as_of_mode == "question-date" and parsed is None:
        raise ValueError(f"Question date missing/unparseable for {qid}")
    as_of = parsed[0] if as_of_mode == "question-date" else None
    started = time.perf_counter()
    mem = Memory(embedder=embedder)
    ingest(mem, item, qid)
    # Prewarm the shared query vector. Retrieval latencies exclude ingestion/embedding.
    mem.embedder.embed(item["question"])
    setup_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    dense, lexical = prepare_candidates(mem, item["question"], qid, as_of)
    shared_ms = (time.perf_counter() - started) * 1000
    dense_rank = {str(e.session_id): i for i, e in enumerate(dense, 1)}
    lexical_rank = {str(e.session_id): i for i, e in enumerate(lexical, 1)}
    row.update(status="scored", eligible_sessions=len(dense), setup_ms=setup_ms,
               candidate_shared_ms=shared_ms, results={}, gold_diagnostics={})
    for k in KS:
        started = time.perf_counter()
        baseline = mem.retrieve_episodes(item["question"], qid, k, as_of=as_of)
        baseline_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        candidate, ranking = independent_rrf(dense, lexical, k)
        candidate_ms = shared_ms + (time.perf_counter() - started) * 1000
        row["results"][str(k)] = {}
        for label, eps, latency in (("baseline", baseline, baseline_ms), ("independent_bm25_rrf", candidate, candidate_ms)):
            ids = [str(e.session_id) for e in eps]
            row["results"][str(k)][label] = dict(
                coverage(ids, gold), selected_session_ids=ids, latency_ms=latency,
                returned_chars=sum(len(e.content) for e in eps))
        final_ranks = {str(e.session_id): i for i, e in enumerate(ranking, 1)}
        base_ranks = {str(e.session_id): i for i, e in enumerate(baseline, 1)}
        row["gold_diagnostics"][str(k)] = {
            sid: {"dense_rank": dense_rank.get(sid), "positive_bm25_rank": lexical_rank.get(sid),
                  "outside_dense_pool": dense_rank.get(sid, math.inf) > max(k * 5, 25),
                  "absent_or_time_filtered": sid not in dense_rank,
                  "baseline_return_rank": base_ranks.get(sid), "candidate_rank": final_ranks.get(sid)}
            for sid in sorted(gold)}
    # This is an ideal evidence reference, not a QA run or a budget-matched retrieval arm.
    row["full_context_oracle_reference"] = dict(coverage(gold, gold),
                                               actual_eligible_coverage=coverage(dense_rank, gold))
    return row


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    temporary.replace(path)


def make_manifest(dataset, items, model, flags):
    sources = (Path(__file__), ROOT / "engram/memory.py", ROOT / "engram/retrieve/lexical.py",
               ROOT / "engram/util.py", ROOT / "engram/store/memory_store.py", ROOT / "eval/longmemeval.py")
    selector = {str(p.relative_to(ROOT)): file_hash(p) for p in sources}
    body = {"schema": 1, "measurement": "session_evidence_coverage_not_qa", "dataset": dataset,
            "selected_qids": [str(i["question_id"]) for i in items], "model": model,
            "flags": flags, "selector_sha256": digest(selector), "selector_sources": selector}
    return dict(body, manifest_id=digest(body))


def open_run(out: Path, manifest, resume: bool):
    """Reject mixed configurations, duplicate rows, and torn rows; never silently overwrite."""
    sidecar = out.with_suffix(out.suffix + ".manifest.json")
    if out.exists() or sidecar.exists():
        if not resume:
            raise ValueError("Output already exists; use --resume with the identical configuration")
        if not sidecar.exists() or json.loads(sidecar.read_text()) != manifest:
            raise ValueError("Resume manifest mismatch (dataset, model, selector, or flags changed)")
        rows = []
        if out.exists():
            with out.open() as handle:
                for line in handle:
                    if not line.endswith("\n"):
                        raise ValueError("Torn final JSONL row; repair it explicitly before resuming")
                    rows.append(json.loads(line))
        ids = [r["qid"] for r in rows]
        if ids != manifest["selected_qids"][:len(ids)] or any(r.get("manifest_id") != manifest["manifest_id"] for r in rows):
            raise ValueError("Resume rows are not a valid unique prefix of the selected dataset")
        return rows
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(sidecar, manifest)
    return []


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def summarize(rows, manifest):
    scored = [r for r in rows if r["status"] == "scored"]
    groups = defaultdict(list)
    groups["all"] = scored
    for row in scored:
        groups[row["category"]].append(row)
    summary = {"manifest_id": manifest["manifest_id"], "measurement": "evidence coverage; not QA accuracy",
               "selected": len(manifest["selected_qids"]), "completed": len(rows), "scored": len(scored),
               "excluded_abs": sum(r["status"] == "excluded_abs" for r in rows),
               "excluded_no_gold": sum(r["status"] == "excluded_no_gold" for r in rows),
               "complete": len(rows) == len(manifest["selected_qids"]), "groups": {},
               "latency_scope": "warm-vector retrieval only; candidate includes shared full BM25/dense preparation per k",
               "candidate_scope": "compound change: independent lexical candidates + positive-only/missing-path RRF votes",
               "full_context_oracle_reference": {"n": len(scored), "any_hit": 1 if scored else None,
                   "all_hit": 1 if scored else None, "fractional": 1 if scored else None,
                   "note": "ideal access to every annotated gold session; no QA executed"}}
    for group, subset in groups.items():
        summary["groups"][group] = {"n": len(subset), "k": {}}
        for k in KS:
            arms = {}
            for arm in ("baseline", "independent_bm25_rrf"):
                cells = [r["results"][str(k)][arm] for r in subset]
                arms[arm] = {metric: sum(c[metric] for c in cells) / len(cells) if cells else None
                             for metric in ("any_hit", "all_hit", "fractional", "returned_chars")}
                arms[arm].update({"p50_ms": percentile([c["latency_ms"] for c in cells], .5),
                                  "p95_ms": percentile([c["latency_ms"] for c in cells], .95)})
            summary["groups"][group]["k"][str(k)] = arms
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="s")
    parser.add_argument("--limit", type=int, default=0, help="0 = full dataset, otherwise ordered prefix")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--embedder", choices=("bge-small", "hashing"), default="bge-small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "data/evidence_coverage_embeddings")
    parser.add_argument("--as-of", choices=("none", "question-date"), default="none")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    items, dataset = load_dataset(args.data)
    items = items[:args.limit] if args.limit else items
    if len({str(i["question_id"]) for i in items}) != len(items):
        raise ValueError("Dataset contains duplicate question IDs")
    model, factory = model_spec(args.embedder, args.device)
    flags = {"limit": args.limit, "ks": list(KS), "as_of": args.as_of, "rrf_k": RRF_K,
             "dense_pool": "max(k*5,25)", "lexical_pool": "all eligible positive-score sessions",
             "reranker": None, "cache_dir": str(args.cache_dir.resolve())}
    manifest = make_manifest(dataset, items, model, flags)
    rows = open_run(args.out, manifest, args.resume)
    embedder = CachedEmbedder(factory, model, args.cache_dir)
    print(f"coverage only | selected={len(items)} resumed={len(rows)} model={args.embedder} as_of={args.as_of}", flush=True)
    with args.out.open("a") as handle:
        for item in items[len(rows):]:
            row = evaluate_item(item, embedder, args.as_of)
            row["manifest_id"] = manifest["manifest_id"]
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            rows.append(row)
            if len(rows) % 10 == 0 or len(rows) == len(items):
                print(f"{len(rows)}/{len(items)} | vectors cache hits={embedder.hits} misses={embedder.misses}", flush=True)
    summary = summarize(rows, manifest)
    summary_path = args.out.with_suffix(args.out.suffix + ".summary.json")
    atomic_json(summary_path, summary)
    print(json.dumps({"scored": summary["scored"], "excluded_abs": summary["excluded_abs"],
                      "complete": summary["complete"], "summary": str(summary_path)}), flush=True)


if __name__ == "__main__":
    main()
