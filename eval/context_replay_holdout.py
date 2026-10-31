"""Run the registered remaining-answerable validation without changing the candidate.

python eval/context_replay_holdout.py \
    --registration results/context-holdout-20261030.preregistered.json \
    --frozen-dir data/context-holdout-20261030 \
    --out results/context-holdout-20261030.jsonl --device mps

Use --replay-only and a new --out to repack already frozen inputs without datasets,
embedding or LLM access. A normal restart also reuses frozen invalid samples; it only
freezes QIDs that have never been attempted. These are source diagnostics, NOT QA.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import context_replay as cr
from eval.context_replay_priority import priority_pack

SCOPE = "remaining_answerable_validation_after_development60"
CANDIDATE_PATH = "eval/context_replay_priority.py"
RUNNER_PATH = "eval/context_replay_holdout.py"


def resolve(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def read_registration(path):
    """Check registration and both prior manifests before any dataset/model work."""
    reg = json.loads(path.read_text())
    dev_ids, holdout_ids = reg["development_qids"], reg["selected_qids"]
    if (len(dev_ids) != 60 or len(set(dev_ids)) != 60
            or len(holdout_ids) != 410 or len(set(holdout_ids)) != 410):
        raise ValueError("Registration must contain unique development60 and holdout410 QIDs")
    if set(dev_ids) & set(holdout_ids):
        raise ValueError("Development and validation QIDs overlap")
    if reg["measurement_scope"] != SCOPE or reg["rig"] != cr.RIG or reg["budgets"] != list(cr.BUDGETS):
        raise ValueError("Registered scope, rig or budgets differ from the frozen experiment")
    if sum(reg["counts_by_category"].values()) != 410:
        raise ValueError("Registered category counts do not sum to 410")
    if reg["candidate_source"]["path"] != CANDIDATE_PATH:
        raise ValueError("Registration does not identify the fixed priority candidate")
    if cr.file_hash(resolve(CANDIDATE_PATH)) != reg["candidate_source"]["sha256"]:
        raise ValueError("Candidate source drift")
    manifests = {}
    for role in ("development", "candidate"):
        source = resolve(reg[role + "_manifest_path"])
        if cr.file_hash(source) != reg[role + "_manifest_sha256"]:
            raise ValueError(role + " manifest file hash mismatch")
        manifest = json.loads(source.read_text())
        cr.validate_manifest(manifest)
        if manifest["manifest_id"] != reg[role + "_manifest_id"]:
            raise ValueError(role + " manifest identity mismatch")
        for key in ("dataset", "rig", "budgets"):
            if manifest[key] != reg[key]:
                raise ValueError(role + " manifest differs from registered " + key)
        if manifest["selected_qids"] != dev_ids:
            raise ValueError(role + " manifest differs from development QIDs")
        manifests[role] = manifest
    candidate = manifests["candidate"]
    if (candidate.get("candidate_source") != reg["candidate_source"]
            or candidate.get("parent_manifest_id") != reg["development_manifest_id"]):
        raise ValueError("Candidate was not frozen against the registered development manifest")
    if candidate["embedding_model"] != manifests["development"]["embedding_model"]:
        raise ValueError("Prior embedding identities differ")
    return reg, manifests["development"]


def validate_selection(items, dataset, reg):
    """Recompute the exact complement without inspecting question/answer content."""
    if dataset["sha256"] != reg["dataset"]["sha256"]:
        raise ValueError("Dataset hash differs from registration")
    if Path(dataset["resolved_path"]).resolve() != Path(reg["dataset"]["resolved_path"]).resolve():
        raise ValueError("Dataset cache path differs from registration")
    by_id = {str(item["question_id"]): item for item in items}
    if len(by_id) != len(items):
        raise ValueError("Dataset contains duplicate QIDs")
    eligible = {qid for qid, item in by_id.items()
                if not qid.endswith("_abs") and item.get("answer_session_ids")}
    development = set(reg["development_qids"])
    if not development <= eligible or eligible - development != set(reg["selected_qids"]):
        raise ValueError("Registered validation QIDs are not the complete answerable complement")
    selected = [by_id[qid] for qid in reg["selected_qids"]]
    counts = dict(Counter(item["question_type"] for item in selected))
    if counts != reg["counts_by_category"]:
        raise ValueError("Registered category counts differ from dataset")
    return selected


def build_manifest(reg, registration, model, selected):
    return cr.seal_manifest({
        "schema": 2, "measurement": "frozen_context_packing_diagnostics_not_qa",
        "measurement_scope": reg["measurement_scope"], "dataset": reg["dataset"],
        "selected_qids": reg["selected_qids"], "rig": reg["rig"], "budgets": reg["budgets"],
        "counts_by_category": reg["counts_by_category"],
        "category_by_qid": {str(i["question_id"]): i["question_type"] for i in selected},
        "candidate": "existing_summary_fallback_prepend_v1", "candidate_source": reg["candidate_source"],
        "embedding_model": model, "llm_model_tag": cr.MODEL_TAG, "llm_cache": str(cr.CACHE_DIR),
        "registration_path": str(registration.resolve()), "registration_sha256": cr.file_hash(registration),
        "runner_source": {"path": RUNNER_PATH, "sha256": cr.file_hash(resolve(RUNNER_PATH))},
        "development_manifest_id": reg["development_manifest_id"],
        "candidate_manifest_id": reg["candidate_manifest_id"],
        "selection_verified": "complete_non_abs_with_gold_minus_registered_development60",
    })


def validate_runtime(manifest, registration, reg):
    cr.validate_manifest(manifest)
    if (manifest["runner_source"]["path"] != RUNNER_PATH
            or manifest["runner_source"]["sha256"] != cr.file_hash(resolve(RUNNER_PATH))):
        raise ValueError("Holdout runner source drift; use a new frozen directory")
    if cr.file_hash(resolve(CANDIDATE_PATH)) != manifest["candidate_source"]["sha256"]:
        raise ValueError("Candidate source drift")
    if manifest["registration_sha256"] != cr.file_hash(registration):
        raise ValueError("Registration changed")
    for key in ("dataset", "selected_qids", "rig", "budgets", "candidate_source", "measurement_scope"):
        if manifest[key] != reg[key]:
            raise ValueError("Frozen manifest differs from registration: " + key)


def seal_sample(frozen, manifest):
    frozen = dict(frozen, manifest_id=manifest["manifest_id"], qid_sha256=cr.text_hash(frozen["qid"]))
    return dict(frozen, frozen_payload_sha256=cr.digest(frozen))


def validate_sample(frozen, qid, manifest):
    if (frozen.get("qid") != qid or frozen.get("qid_sha256") != cr.text_hash(qid)
            or frozen.get("manifest_id") != manifest["manifest_id"]):
        raise cr.FrozenSampleIdentityError("Frozen QID/hash/manifest identity mismatch")
    payload = {key: value for key, value in frozen.items() if key != "frozen_payload_sha256"}
    if frozen.get("frozen_payload_sha256") != cr.digest(payload):
        raise cr.FrozenSampleIdentityError("Frozen payload hash mismatch")


def run_samples(selected, embedder, manifest, reg, registration, frozen_dir, out):
    rows = []
    with out.open("x") as handle:
        for item in selected:
            validate_runtime(manifest, registration, reg)
            qid = str(item["question_id"])
            path = cr.frozen_path(frozen_dir, qid)
            frozen = {}
            try:
                if path.exists():
                    frozen = json.loads(path.read_text())
                elif embedder is None:
                    frozen = seal_sample({"qid": qid, "category": item["question_type"],
                                          "status": "invalid", "error_class": "MissingFrozenSample",
                                          "cache_failures": []}, manifest)
                else:
                    frozen = seal_sample(cr.freeze_item(item, embedder), manifest)
                    cr.atomic_json(path, frozen)
                validate_sample(frozen, qid, manifest)
                row = cr.replay_frozen(frozen, packer=priority_pack) if frozen["status"] == "frozen" else dict(frozen)
            except Exception as exc:
                row = {"qid": qid, "category": item["question_type"], "status": "invalid",
                       "error_class": type(exc).__name__, "error_sha256": cr.text_hash(str(exc)),
                       "cache_failures": frozen.get("cache_failures", []) if isinstance(frozen, dict) else []}
            row.update(manifest_id=manifest["manifest_id"], measurement_scope=reg["measurement_scope"])
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(json.dumps({"completed": len(rows), "selected": len(selected),
                              "qid": qid, "status": row["status"]}), flush=True)
        summary = cr.summarize(rows)
        summary.update(manifest_id=manifest["manifest_id"], measurement_scope=reg["measurement_scope"],
                       candidate=manifest["candidate"])
        handle.write(json.dumps(summary, ensure_ascii=False) + "\n")
        handle.flush()
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--frozen-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--replay-only", action="store_true")
    args = parser.parse_args(argv)
    sidecar = args.out.with_suffix(args.out.suffix + ".manifest.json")
    if args.out.exists() or sidecar.exists():
        raise ValueError("Output/manifest exists; choose a new output path")
    reg, development = read_registration(args.registration)
    path = args.frozen_dir / "manifest.json"
    existing = json.loads(path.read_text()) if path.exists() else None
    if existing is not None:
        validate_runtime(existing, args.registration, reg)
    if args.replay_only:
        if existing is None:
            raise ValueError("Frozen manifest missing")
        manifest, embedder = existing, None
        selected = [{"question_id": qid, "question_type": manifest["category_by_qid"][qid]}
                    for qid in reg["selected_qids"]]
    else:
        os.environ["HF_HUB_OFFLINE"] = os.environ["TRANSFORMERS_OFFLINE"] = "1"
        items, dataset = cr.load_dataset(reg["dataset"]["resolved_path"])
        selected = validate_selection(items, dataset, reg)
        model, factory = cr.model_spec("bge-small", args.device)
        if model != development["embedding_model"]:
            raise ValueError("Embedding identity differs from registered development configuration")
        manifest = build_manifest(reg, args.registration, model, selected)
        if existing is not None and existing != manifest:
            raise ValueError("Frozen manifest differs; use a new directory")
        cr.atomic_json(path, manifest)
        embedder = cr.CachedEmbedder(factory, model, ROOT / "data/evidence_coverage_embeddings")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cr.atomic_json(sidecar, manifest)
    run_samples(selected, embedder, manifest, reg, args.registration, args.frozen_dir, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
