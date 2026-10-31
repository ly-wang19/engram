"""Analyze a completed frozen-context run; packing times are diagnostics, not QA latency.

python eval/context_replay_report.py --run results/context-replay-20261030.jsonl \
    --frozen-dir data/context-replay-20261030 --out results/context-replay-20261030.analysis.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engram import Memory
from engram.config import Config
from engram.retrieve.evidence import EvidenceNeed
from engram.types import Episode
from eval import context_replay as cr
from eval.evidence_coverage import file_hash
from eval.longmemeval import all_text, sessions_of, FC_CHAR_BUDGET


def sources(metrics):
    return set(metrics["gold_complete_raw_ids"]) | set(metrics["gold_complete_summary_ids"])


def percentile(values, percent):
    values = sorted(values)
    return values[round((len(values) - 1) * percent)] if values else None


def primary_gate(summary, paired):
    """A positive net total cannot hide a paired loss or missing validation samples."""
    if paired["source_loss_qids"] or paired["literal_loss_qids"]:
        decision = "reject_primary_regression"
    elif summary["invalid"]:
        decision = "inconclusive_incomplete_cache"
    elif paired["source_gain_qids"]:
        decision = "retain_for_qa"
    else:
        decision = "no_observed_primary_gain"
    return {"decision": decision, "budget_chars": 60000,
            "qa_accuracy_established": False, "production_default_authorized": False}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--frozen-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    rows = [json.loads(line) for line in args.run.read_text().splitlines()]
    summary = rows.pop()
    manifest = json.loads(args.run.with_suffix(args.run.suffix + ".manifest.json").read_text())
    cr.validate_manifest(manifest)
    if manifest.get("runner_source"):
        runner = manifest["runner_source"]
        assert runner["path"] == "eval/context_replay_holdout.py"
        assert runner["sha256"] == file_hash(cr.ROOT / runner["path"])
    packer = Memory._pack_lean_blocks
    if manifest.get("candidate_source"):
        candidate = manifest["candidate_source"]
        assert candidate["path"] == "eval/context_replay_priority.py"
        assert candidate["sha256"] == file_hash(cr.ROOT / candidate["path"])
        from eval.context_replay_priority import priority_pack

        packer = priority_pack
    computed = cr.summarize(rows)
    assert {key: summary[key] for key in computed} == computed
    if "manifest_id" in summary:
        assert summary["manifest_id"] == manifest["manifest_id"]
    assert [r["qid"] for r in rows] == manifest["selected_qids"]
    valid = [r for r in rows if r["status"] == "valid"]
    validation = manifest.get("measurement_scope") == "remaining_answerable_validation_after_development60"
    report = dict(summary, run_sha256=file_hash(args.run),
                  reporter_sha256=file_hash(Path(__file__)),
                  measurement_scope=manifest.get("measurement_scope", "development60"),
                  limitations=["Source completeness is not semantic answer coverage or QA correctness.",
                               "Short reference substring is diagnostic, not a judge.",
                               ("Remaining items of the same public benchmark after development60, not a new independent benchmark."
                                if validation else "Stratified development sample, not a held-out full benchmark."),
                               "Characters are not tokens; no answerer or judge was run."],
                  total_cache_hits=sum(r.get("cache_hits", 0) for r in rows),
                  paired={}, packing_latency_scope="local helper only; 3 alternating-order warm passes")
    latencies = {(b, a): [] for b in cr.BUDGETS for a in ("baseline", "candidate")}
    for row in valid:
        frozen = json.loads(cr.frozen_path(args.frozen_dir, row["qid"]).read_text())
        assert cr.digest(frozen) == row["input_sha256"]
        mem = Memory.__new__(Memory)
        mem.config = Config(**frozen["config"])
        need = EvidenceNeed(**frozen["need"]) if frozen["need"] is not None else None
        eps = [Episode(**ep) for ep in frozen["detail_eps"]]
        for budget in cr.BUDGETS:
            for repeat in range(3):
                arms = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
                for arm in arms:
                    mem.config.packing_summary_fallback = arm == "candidate"
                    start = time.perf_counter()
                    context = packer(mem, list(frozen["blocks"]), budget, need, eps,
                                     default_budget=budget == 60000)
                    elapsed = (time.perf_counter() - start) * 1000
                    assert cr.text_hash(context) == row["budgets"][str(budget)][arm]["context_sha256"]
                    latencies[budget, arm].append(elapsed)
    for budget in cr.BUDGETS:
        paired = dict(source_gain_qids=[], source_loss_qids=[], literal_gain_qids=[],
                      literal_loss_qids=[], changed_context_qids=[], baseline_all_sources=0,
                      candidate_all_sources=0, assembled_all_sources=0, gold_source_total=0,
                      assembled_gold_sources=0, added_gold_sources=0, lost_gold_sources=0,
                      literal_eligible=0, assembled_literal_present=0,
                      baseline_lost_assembled_literal=0, candidate_lost_assembled_literal=0)
        for row in valid:
            before = row["assembled"]
            base, cand = (row["budgets"][str(budget)][a] for a in ("baseline", "candidate"))
            a, b = sources(base), sources(cand)
            paired["gold_source_total"] += before["gold_session_count"]
            paired["assembled_gold_sources"] += len(sources(before))
            paired["assembled_all_sources"] += len(sources(before)) == before["gold_session_count"]
            paired["baseline_all_sources"] += len(a) == base["gold_session_count"]
            paired["candidate_all_sources"] += len(b) == cand["gold_session_count"]
            paired["added_gold_sources"] += len(b - a)
            paired["lost_gold_sources"] += len(a - b)
            for name, condition in (("source_gain_qids", bool(b - a)), ("source_loss_qids", bool(a - b)),
                                    ("changed_context_qids", base["context_sha256"] != cand["context_sha256"])):
                if condition:
                    paired[name].append(row["qid"])
            x, y, z = (m["literal_reference_diagnostic_not_qa"] for m in (before, base, cand))
            if x["eligible"]:
                paired["literal_eligible"] += 1
                paired["assembled_literal_present"] += x["retained"]
                paired["baseline_lost_assembled_literal"] += x["retained"] and not y["retained"]
                paired["candidate_lost_assembled_literal"] += x["retained"] and not z["retained"]
                if z["retained"] and not y["retained"]:
                    paired["literal_gain_qids"].append(row["qid"])
                if y["retained"] and not z["retained"]:
                    paired["literal_loss_qids"].append(row["qid"])
        paired["packing_latency_ms"] = {a: {"p50": statistics.median(latencies[budget, a]),
                                            "p95": percentile(latencies[budget, a], .95)}
                                       for a in ("baseline", "candidate")} if valid else {}
        report["paired"][str(budget)] = paired
    if validation:
        report["pre_registered_primary_gate"] = primary_gate(summary, report["paired"]["60000"])
    items, dataset = cr.load_dataset("s")
    assert dataset == manifest["dataset"]
    by_id = {str(i["question_id"]): i for i in items}
    reference = []
    for row in valid:
        item = by_id[row["qid"]]
        full = all_text(item)[:FC_CHAR_BUDGET]
        answer = cr.normalize(str(item.get("answer", "")))
        eligible = row["assembled"]["literal_reference_diagnostic_not_qa"]["eligible"]
        reference.append({"chars": len(full), "literal": eligible and answer in cr.normalize(full),
                          "gold_sources": len(set(map(str, item["answer_session_ids"])) &
                                              {str(sid) for sid, _ in sessions_of(item)}),
                          "truncated": len(all_text(item)) > FC_CHAR_BUDGET})
    report["full_context_reference_not_qa"] = {
        "samples": len(reference), "mean_chars": statistics.mean(r["chars"] for r in reference) if reference else None,
        "literal_present": sum(r["literal"] for r in reference),
        "gold_sources_in_input": sum(r["gold_sources"] for r in reference),
        "truncated_samples": sum(r["truncated"] for r in reference),
        "latency": None, "tokens": None}
    cr.atomic_json(args.out, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
