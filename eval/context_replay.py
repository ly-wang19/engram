"""Freeze one cache-only read, then replay packing on identical inputs. NOT QA accuracy.

    python eval/context_replay.py --per-category 10 --device mps \
        --frozen-dir data/context-replay-20261030 --out results/context-replay.jsonl
    python eval/context_replay.py --replay-only --frozen-dir data/context-replay-20261030 \
        --out results/context-replay-again.jsonl

Public benchmark text stays in the frozen data directory, never the result JSONL.
No provider is instantiated; a missing/empty/unreadable LLM reply invalidates the sample,
including when production code catches that error and falls back. Embedding is local-only.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import threading
from types import MethodType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engram import Memory
from engram.config import Config
from engram.llm.base import LLM
from engram.llm.cache import CachedLLM
from engram.retrieve.evidence import EvidenceNeed
from engram.types import Episode
from engram.util import fmt_date
from eval.evidence_coverage import CachedEmbedder, atomic_json, digest, file_hash, load_dataset, model_spec

MODEL_TAG = "volcano:doubao-seed-1-6-flash-250615"
CACHE_DIR = ROOT / "results/pin_ab_2026-09-11"
SELECTOR = "context-packing-v1:"
CATEGORIES = ("knowledge-update", "multi-session", "single-session-assistant",
              "single-session-preference", "single-session-user", "temporal-reasoning")
RIG = dict(topk=15, chunks=2, extract_k=8, summ_k=28, n_summaries=28,
           persona=True, reasoning=True)
BUDGETS = (60_000, 32_000, 16_000)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def select_items(items, per_category=10):
    """Selection sees category, QID and gold-session availability, never answer content."""
    if per_category < 1:
        raise ValueError("per_category must be positive")
    selected = []
    for category in CATEGORIES:
        eligible = [i for i in items if i.get("question_type") == category
                    and not str(i["question_id"]).endswith("_abs") and i.get("answer_session_ids")]
        eligible.sort(key=lambda i: (text_hash(SELECTOR + str(i["question_id"])), str(i["question_id"])))
        selected.extend(eligible[:per_category])
    qids = [str(i["question_id"]) for i in selected]
    if len(qids) != len(set(qids)):
        raise ValueError("Duplicate selected QID")
    return selected


class CacheUnavailable(RuntimeError):
    pass


class CacheOnlyLLM(LLM):
    """Read CachedLLM's exact keys/replies without its write-through/network path."""

    def __init__(self, directory=CACHE_DIR):
        # Bypass CachedLLM.__init__: even mkdir is forbidden for this read-only cache.
        self.cache = CachedLLM.__new__(CachedLLM)
        self.cache.path, self.cache.model_tag = str(directory), MODEL_TAG
        self.lock = threading.Lock()
        self.used = {}
        self.failures = []
        self.hits = 0

    def complete(self, prompt, **kwargs):
        key = self.cache._key(prompt, kwargs)
        reply = self.cache._read(key)
        if reply is None or not reply.strip():
            with self.lock:
                self.failures.append({"cache_key": key, "reason": "missing_empty_or_unreadable"})
            raise CacheUnavailable("Offline LLM cache unavailable: " + key)
        reply_sha = text_hash(reply)
        with self.lock:
            if key in self.used and self.used[key] != reply_sha:
                self.failures.append({"cache_key": key, "reason": "reply_changed_during_freeze"})
                raise CacheUnavailable("Offline LLM cache changed: " + key)
            self.used[key] = reply_sha
            self.hits += 1
        return reply

    def receipt(self):
        with self.lock:
            return {"cache_hits": self.hits, "cache_failures": list(self.failures),
                    "used_cache_keys": [{"key": k, "reply_sha256": v}
                                        for k, v in sorted(self.used.items())]}


def episode_payload(ep):
    value = asdict(ep)
    # Vectors are not needed to replay packing and must not trigger an embedding read.
    value["embedding"] = value["summary_embedding"] = None
    return value


def freeze_item(item, embedder, llm=None, system_factory=None):
    """One read with packing intercepted; sticky cache failures defeat swallowed fallback."""
    if not hasattr(Memory, "_pack_lean_blocks"):
        raise RuntimeError("Memory._pack_lean_blocks helper is not available yet")
    from eval.bench import EngramLeanSystem, Rig

    llm = llm if llm is not None else CacheOnlyLLM()
    row = {"qid": str(item["question_id"]), "category": item.get("question_type", "unknown")}
    captured = {}
    try:
        rig = Rig(embedder, llm, None, None, **RIG)
        system = (system_factory or EngramLeanSystem)(rig)
        mem = system._mem_for(item)
        original = mem._pack_lean_blocks

        def capture(_self, blocks, budget, need, detail_eps, *, default_budget=False):
            if captured:
                raise RuntimeError("Packing helper invoked more than once")
            captured.update(blocks=list(blocks), need=asdict(need) if need else None,
                            detail_eps=[episode_payload(e) for e in detail_eps])
            return ""

        mem._pack_lean_blocks = MethodType(capture, mem)
        try:
            mem.lean_context(item["question"], user_id=row["qid"], n_summaries=rig.n_summaries,
                             n_facts=rig.topk, n_chunks=rig.chunks, persona=rig.persona,
                             agentic=rig.agentic, cascade=rig.cascade, timeline=rig.timeline,
                             char_budget=1)
        finally:
            mem._pack_lean_blocks = original
        if not captured:
            raise RuntimeError("Packing helper was not invoked")
        if llm.failures:
            raise CacheUnavailable("A swallowed cache failure invalidates this sample")
        summary_refs = [{"session_id": str(e.session_id), "summary": e.summary,
                         "date": e.metadata.get("date") or fmt_date(e.event_time)}
                        for e in mem.episodes_doc.values() if e.summary]
        frozen = dict(row, schema=1, status="frozen", **captured, config=asdict(mem.config),
                      summary_refs=summary_refs, gold_session_ids=sorted(map(str, item["answer_session_ids"])),
                      reference_answer=str(item.get("answer", "")), **llm.receipt())
        return frozen
    except Exception as exc:
        # Exception text can contain benchmark text; only class and hash are public diagnostics.
        return dict(row, status="invalid", error_class=type(exc).__name__,
                    error_sha256=text_hash(str(exc)), **llm.receipt())


def normalize(text):
    return " ".join(text.split()).casefold()


def context_metrics(context, frozen):
    raw = {str(e["session_id"]) for e in frozen["detail_eps"]
           if e["content"] and (f"[{e['metadata'].get('date') or fmt_date(e['event_time'])}] "
                                f"(session: {e['session_id']})\n{e['content']}") in context}
    summaries = {e["session_id"] for e in frozen["summary_refs"]
                 if e["summary"] and f"- [{e['date']}] {e['summary']}" in context}
    gold = set(frozen["gold_session_ids"])
    answer = normalize(frozen["reference_answer"])
    eligible = bool(answer) and len(answer) <= 120 and any(c.isalpha() for c in answer)
    return {"context_chars": len(context), "context_sha256": text_hash(context),
            "complete_raw_sessions": len(raw), "complete_summary_sessions": len(summaries),
            "gold_complete_raw_ids": sorted(raw & gold),
            "gold_complete_summary_ids": sorted(summaries & gold),
            "gold_raw_or_summary_count": len((raw | summaries) & gold), "gold_session_count": len(gold),
            "literal_reference_diagnostic_not_qa": {
                "eligible": eligible, "retained": answer in normalize(context) if eligible else None}}


def replay_frozen(frozen, packer=None):
    """No dataset, model, embedding, retrieval or LLM access: only frozen packing inputs."""
    if frozen.get("status") != "frozen" or frozen.get("cache_failures"):
        raise ValueError("Cannot replay an invalid freeze")
    mem = Memory.__new__(Memory)
    mem.config = Config(**frozen["config"])
    need = EvidenceNeed(**frozen["need"]) if frozen["need"] is not None else None
    assembled = "\n\n".join(frozen["blocks"])
    result = {"qid": frozen["qid"], "category": frozen["category"], "status": "valid",
              "measurement": "frozen_context_packing_diagnostics_not_qa",
              "input_sha256": digest(frozen), "cache_hits": frozen["cache_hits"],
              "cache_failures": [], "used_cache_keys": frozen["used_cache_keys"],
              "assembled": context_metrics(assembled, frozen), "budgets": {}}
    pack = packer or Memory._pack_lean_blocks
    for budget in BUDGETS:
        arms = {}
        for label, enabled in (("baseline", False), ("candidate", True)):
            mem.config.packing_summary_fallback = enabled
            eps = [Episode(**e) for e in frozen["detail_eps"]]
            context = pack(mem, list(frozen["blocks"]), budget, need, eps,
                           default_budget=budget == 60_000)
            if len(context) > budget:
                raise ValueError("Packer exceeded character budget")
            arms[label] = dict(context_metrics(context, frozen), packing_occurred=context != assembled,
                               budget_chars=budget)
        result["budgets"][str(budget)] = arms
    return result


def summarize(rows):
    valid = [r for r in rows if r["status"] == "valid"]
    summary = {"type": "summary", "measurement": "frozen_context_packing_diagnostics_not_qa",
               "selected": len(rows), "valid": len(valid), "invalid": len(rows) - len(valid),
               "not_full_500": True, "primary_budget_chars": 60_000, "budgets": {}}
    summary["by_category"] = {category: {
        "selected": sum(r.get("category") == category for r in rows),
        "valid": sum(r.get("category") == category for r in valid),
        "invalid": sum(r.get("category") == category and r["status"] != "valid" for r in rows),
    } for category in CATEGORIES}
    for budget in BUDGETS:
        arms = {}
        for arm in ("baseline", "candidate"):
            metrics = [r["budgets"][str(budget)][arm] for r in valid]
            literal = [m["literal_reference_diagnostic_not_qa"] for m in metrics]
            eligible = [m for m in literal if m["eligible"]]
            arms[arm] = {"valid_denominator": len(valid),
                         "packing_samples": sum(m["packing_occurred"] for m in metrics),
                         "mean_context_chars": sum(m["context_chars"] for m in metrics) / len(metrics) if metrics else None,
                         "complete_raw_sessions": sum(m["complete_raw_sessions"] for m in metrics),
                         "complete_summary_sessions": sum(m["complete_summary_sessions"] for m in metrics),
                         "gold_raw_or_summary_count": sum(m["gold_raw_or_summary_count"] for m in metrics),
                         "literal_reference_eligible": len(eligible),
                         "literal_reference_retained": sum(m["retained"] for m in eligible)}
        summary["budgets"][str(budget)] = arms
    return summary


def frozen_path(directory, qid):
    return directory / (text_hash(qid) + ".json")


def source_fingerprints():
    # Include the entire read/freeze implementation, not just the selection function.
    paths = ("engram/memory.py", "engram/config.py", "engram/retrieve/evidence.py",
             "eval/context_replay.py", "eval/bench.py", "eval/evidence_coverage.py",
             "eval/longmemeval.py", "engram/llm/cache.py")
    return {path: file_hash(ROOT / path) for path in paths}


def seal_manifest(body):
    sources = source_fingerprints()
    body = dict(body, source_sha256=sources, source_fingerprint_sha256=digest(sources))
    return dict(body, manifest_id=digest(body))


def validate_manifest(manifest):
    sources = source_fingerprints()
    if (manifest.get("source_sha256") != sources
            or manifest.get("source_fingerprint_sha256") != digest(sources)):
        raise ValueError("Frozen source fingerprint drift; use a new frozen directory")
    body = {key: value for key, value in manifest.items() if key != "manifest_id"}
    if manifest.get("manifest_id") != digest(body):
        raise ValueError("Frozen manifest integrity mismatch; use a new frozen directory")
    qids = manifest["selected_qids"]
    if len(qids) != len(set(qids)):
        raise ValueError("Duplicate QID in frozen manifest")


class FrozenSampleIdentityError(ValueError):
    pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="s")
    ap.add_argument("--per-category", type=int, default=10)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--frozen-dir", required=True, type=Path)
    ap.add_argument("--replay-only", action="store_true")
    args = ap.parse_args(argv)
    if args.out.exists():
        raise ValueError("Output exists; choose a new path")
    if not hasattr(Memory, "_pack_lean_blocks"):
        raise RuntimeError("Wait for Memory._pack_lean_blocks before running")
    manifest_path = args.frozen_dir / "manifest.json"
    existing_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if existing_manifest is not None:
        validate_manifest(existing_manifest)
    if args.replay_only:
        if existing_manifest is None:
            raise ValueError("Frozen manifest is missing")
        manifest = existing_manifest
        selected = [{"question_id": q} for q in manifest["selected_qids"]]
        embedder = None
    else:
        os.environ["HF_HUB_OFFLINE"] = os.environ["TRANSFORMERS_OFFLINE"] = "1"
        items, dataset = load_dataset(args.data)
        selected = select_items(items, args.per_category)
        model, factory = model_spec("bge-small", args.device)
        manifest = seal_manifest({"schema": 2, "measurement": "frozen_context_packing_diagnostics_not_qa",
                    "dataset": dataset, "selected_qids": [str(i["question_id"]) for i in selected],
                    "rig": RIG, "embedding_model": model, "llm_model_tag": MODEL_TAG,
                    "llm_cache": str(CACHE_DIR), "selector": SELECTOR,
                    "selector_sha256": text_hash(inspect.getsource(select_items)),
                    "per_category": args.per_category, "budgets": list(BUDGETS)})
        if existing_manifest is not None and existing_manifest != manifest:
            raise ValueError("Frozen manifest differs; use another directory")
        atomic_json(manifest_path, manifest)
        embedder = CachedEmbedder(factory, model, ROOT / "data/evidence_coverage_embeddings")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.out.with_suffix(args.out.suffix + ".manifest.json"), manifest)
    rows = []
    with args.out.open("x") as handle:
        for item in selected:
            path = frozen_path(args.frozen_dir, str(item["question_id"]))
            # Refuse source drift even if another process edited files during this run.
            validate_manifest(manifest)
            frozen = {}
            try:
                if path.exists():
                    frozen = json.loads(path.read_text())
                elif args.replay_only:
                    frozen = {"qid": str(item["question_id"]), "status": "invalid",
                              "error_class": "MissingFrozenSample", "cache_failures": [],
                              "manifest_id": manifest["manifest_id"]}
                else:
                    frozen = freeze_item(item, embedder)
                    frozen["manifest_id"] = manifest["manifest_id"]
                    atomic_json(path, frozen)
                if (frozen.get("qid") != str(item["question_id"])
                        or frozen.get("manifest_id") != manifest["manifest_id"]):
                    raise FrozenSampleIdentityError("Frozen QID or manifest identity mismatch")
                row = replay_frozen(frozen) if frozen["status"] == "frozen" else frozen
                row["manifest_id"] = manifest["manifest_id"]
            except Exception as exc:
                row = {"qid": str(item["question_id"]), "status": "invalid",
                       "error_class": type(exc).__name__, "error_sha256": text_hash(str(exc)),
                       "cache_failures": frozen.get("cache_failures", [])}
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(json.dumps({"completed": len(rows), "selected": len(selected),
                              "qid": row["qid"], "status": row["status"]}), flush=True)
        summary = summarize(rows)
        handle.write(json.dumps(summary) + "\n")
        handle.flush()
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
