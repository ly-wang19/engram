"""Replay a final, bounded summary-priority candidate on existing frozen inputs.

    python eval/context_replay_priority.py --frozen-dir data/context-replay-20261030 \
        --run results/context-replay-20261030-repack.jsonl \
        --out results/context-replay-20261030-priority.jsonl

This changes only fallback-summary ordering, not retrieval or extraction. No dataset,
model, embedding, or LLM is loaded. Evidence/literal retention is NOT QA accuracy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engram import Memory  # noqa: E402
from engram.util import fmt_date  # noqa: E402
from eval import context_replay as cr  # noqa: E402


def priority_pack(mem, blocks, budget, need, detail_eps, *, default_budget=False):
    """Put missing raw sources' existing summaries before the existing summary body.

    The flag is temporarily disabled only to reuse the unchanged baseline fitter.
    Inputs are copied, source order breaks date ties, and at most one pass per detail
    source can add fallbacks. No query, gold ID, answer, store, or model is consulted.
    """
    original_flag = mem.config.packing_summary_fallback
    if not original_flag:
        return Memory._pack_lean_blocks(mem, blocks, budget, need, detail_eps,
                                        default_budget=default_budget)
    try:
        mem.config.packing_summary_fallback = False

        def pack(parts):
            return Memory._pack_lean_blocks(mem, parts, budget, need, detail_eps,
                                            default_budget=default_budget)

        packed = pack(blocks)
        if len("\n\n".join(blocks)) <= budget:
            return packed
        if budget <= 0:
            return ""
        raw_title = "RELEVANT CONVERSATIONS (full detail):\n"
        sources = {ep.id: ep for ep in detail_eps if ep.summary and ep.summary.strip()}
        fallback_ids = set()
        for _ in range(len(sources)):
            raw_output = packed.partition(raw_title)[2]
            missing = set()
            for eid, ep in sources.items():
                full_raw = (f"[{ep.metadata.get('date') or fmt_date(ep.event_time)}] "
                            f"(session: {ep.session_id})\n{ep.content}")
                if eid not in fallback_ids and full_raw not in raw_output:
                    missing.add(eid)
            if not missing:
                break
            fallback_ids.update(missing)
            fallbacks = "\n".join(
                f"- [{ep.metadata.get('date') or fmt_date(ep.event_time)}] {ep.summary}"
                for ep in sorted((ep for eid, ep in sources.items() if eid in fallback_ids),
                                 key=lambda e: e.event_time)
            )
            revised = list(blocks)
            summary_idx = next((i for i, block in enumerate(revised)
                                if mem._evidence_block_kind(block) == "summaries"), None)
            if summary_idx is not None:
                heading, _, body = revised[summary_idx].partition("\n")
                heading = heading.replace(", chronological", "").replace("chronological", "dated")
                revised[summary_idx] = heading + "\n" + fallbacks + "\n" + body
            else:
                raw_idx = next((i for i, block in enumerate(revised)
                                if block.startswith(raw_title)), len(revised))
                revised.insert(raw_idx, "SESSION SUMMARIES (relevant, with raw fallback):\n" + fallbacks)
            packed = pack(revised)
        return packed
    finally:
        mem.config.packing_summary_fallback = original_flag


def verified_inputs(frozen_dir: Path, run: Path):
    """Validate every parent identity/hash before creating any new output."""
    parent = json.loads((frozen_dir / "manifest.json").read_text())
    cr.validate_manifest(parent)
    run_manifest = json.loads(run.with_suffix(run.suffix + ".manifest.json").read_text())
    cr.validate_manifest(run_manifest)
    if run_manifest != parent:
        raise ValueError("Run and frozen-directory manifests differ")
    records = [json.loads(line) for line in run.read_text().splitlines() if line.strip()]
    rows = [row for row in records if row.get("type") != "summary"]
    if [r.get("qid") for r in rows] != parent["selected_qids"]:
        raise ValueError("Parent run must contain exactly the selected QIDs in order")
    frozen_items = []
    for row in rows:
        if row.get("status") != "valid" or row.get("manifest_id") != parent["manifest_id"]:
            raise ValueError("Parent run has an invalid row or a different manifest")
        frozen = json.loads(cr.frozen_path(frozen_dir, row["qid"]).read_text())
        if (frozen.get("qid") != row["qid"]
                or frozen.get("manifest_id") != parent["manifest_id"]
                or frozen.get("status") != "frozen"
                or frozen.get("cache_failures")
                or cr.digest(frozen) != row.get("input_sha256")):
            raise ValueError("Frozen sample identity/content differs from parent run")
        frozen_items.append(frozen)
    return parent, frozen_items, rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-dir", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    sidecar = args.out.with_suffix(args.out.suffix + ".manifest.json")
    if args.out.exists() or sidecar.exists() or args.out.resolve() == args.run.resolve():
        raise ValueError("Output/manifest exists or aliases the parent; choose a new output path")
    parent, frozen_items, parent_rows = verified_inputs(args.frozen_dir, args.run)
    candidate_source = Path(__file__).resolve()
    candidate_sha = cr.file_hash(candidate_source)
    body = {key: value for key, value in parent.items() if key != "manifest_id"}
    body.update(candidate="existing_summary_fallback_prepend_v1",
                parent_manifest_id=parent["manifest_id"], parent_run_sha256=cr.file_hash(args.run),
                candidate_source={"path": str(candidate_source.relative_to(ROOT)), "sha256": candidate_sha})
    manifest = cr.seal_manifest(body)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cr.atomic_json(sidecar, manifest)
    rows = []
    with args.out.open("x") as handle:
        for frozen, old_row in zip(frozen_items, parent_rows):
            cr.validate_manifest(parent)
            if cr.file_hash(candidate_source) != candidate_sha:
                raise ValueError("Candidate source changed during replay")
            row = cr.replay_frozen(frozen, packer=priority_pack)
            for budget in cr.BUDGETS:
                if (row["budgets"][str(budget)]["baseline"]["context_sha256"]
                        != old_row["budgets"][str(budget)]["baseline"]["context_sha256"]):
                    raise ValueError("Baseline replay hash differs from parent result")
            row.update(manifest_id=manifest["manifest_id"], parent_manifest_id=parent["manifest_id"])
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(json.dumps({"completed": len(rows), "selected": len(frozen_items),
                              "qid": row["qid"], "status": row["status"]}), flush=True)
        summary = cr.summarize(rows)
        summary.update(manifest_id=manifest["manifest_id"], candidate=body["candidate"])
        handle.write(json.dumps(summary, ensure_ascii=False) + "\n")
        handle.flush()
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
