import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pytest

from engram.config import Config
from eval import context_replay as cr
from eval import context_replay_holdout as hr


@pytest.fixture
def registered(tmp_path):
    items = [{"question_id": f"q{i:03}", "question_type": cr.CATEGORIES[i % 6],
              "answer_session_ids": ["source"]} for i in range(470)]
    data = tmp_path / "dataset.json"
    data.write_text(json.dumps(items))
    dataset = {"source": str(data), "resolved_path": str(data), "sha256": cr.file_hash(data)}
    model = {"name": "synthetic-no-model", "dim": 2}
    dev_ids = [i["question_id"] for i in items[:60]]
    remaining = items[60:]
    base = {"selected_qids": dev_ids, "dataset": dataset, "rig": cr.RIG,
            "budgets": list(cr.BUDGETS), "embedding_model": model}
    development = cr.seal_manifest(base)
    dev_path = tmp_path / "development.json"
    cr.atomic_json(dev_path, development)
    source = {"path": hr.CANDIDATE_PATH, "sha256": cr.file_hash(hr.resolve(hr.CANDIDATE_PATH))}
    candidate = cr.seal_manifest(dict(base, candidate_source=source,
                                     parent_manifest_id=development["manifest_id"]))
    cand_path = tmp_path / "candidate.json"
    cr.atomic_json(cand_path, candidate)
    reg = {"schema": 1, "measurement_scope": hr.SCOPE, "dataset": dataset,
           "development_manifest_path": str(dev_path),
           "development_manifest_sha256": cr.file_hash(dev_path),
           "development_manifest_id": development["manifest_id"],
           "candidate_manifest_path": str(cand_path),
           "candidate_manifest_sha256": cr.file_hash(cand_path),
           "candidate_manifest_id": candidate["manifest_id"], "candidate_source": source,
           "development_qids": dev_ids, "selected_qids": [i["question_id"] for i in remaining],
           "counts_by_category": dict(Counter(i["question_type"] for i in remaining)),
           "rig": cr.RIG, "budgets": list(cr.BUDGETS)}
    registration = tmp_path / "registration.json"
    cr.atomic_json(registration, reg)
    manifest = hr.build_manifest(reg, registration, model, remaining)
    return items, reg, registration, manifest


def valid_frozen(qid, category):
    return {"schema": 1, "qid": qid, "category": category, "status": "frozen",
            "blocks": ["FACTS (current, dated):\nSynthetic fact."], "detail_eps": [],
            "summary_refs": [], "gold_session_ids": ["source"], "reference_answer": "Synthetic fact",
            "config": asdict(Config()), "need": None, "cache_hits": 1,
            "used_cache_keys": [{"key": "synthetic", "reply_sha256": "synthetic"}], "cache_failures": []}


def test_exact_remaining_answerable_set_excludes_development_without_reading_answers(registered):
    items, reg, registration, _ = registered
    loaded, _ = hr.read_registration(registration)
    selected = hr.validate_selection(list(reversed(items)), reg["dataset"], loaded)
    assert [i["question_id"] for i in selected] == reg["selected_qids"]
    assert len(selected) == 410
    assert not (set(reg["development_qids"]) & {i["question_id"] for i in selected})
    # A new answerable source is not silently ignored or substituted.
    with pytest.raises(ValueError, match="complete answerable complement"):
        hr.validate_selection(items + [{"question_id": "new", "answer_session_ids": ["s"]}],
                              reg["dataset"], reg)
    # Abstention/no-gold items do not change the registered answerable complement.
    extra = [{"question_id": "q_abs", "answer_session_ids": ["s"]}, {"question_id": "no-gold"}]
    assert len(hr.validate_selection(items + extra, reg["dataset"], reg)) == 410


def test_development_overlap_rejected(registered):
    _, reg, registration, _ = registered
    reg["selected_qids"][0] = reg["development_qids"][0]
    cr.atomic_json(registration, reg)
    with pytest.raises(ValueError, match="overlap"):
        hr.read_registration(registration)


def test_candidate_drift_rejected_before_dataset_or_model(registered, monkeypatch, tmp_path):
    _, _, registration, _ = registered
    actual_hash = cr.file_hash
    monkeypatch.setattr(cr, "file_hash", lambda p: "changed" if Path(p).resolve() == hr.resolve(hr.CANDIDATE_PATH)
                        else actual_hash(p))

    def forbidden(*a, **kw):
        raise AssertionError("Must reject candidate drift before data/model work")

    monkeypatch.setattr(cr, "load_dataset", forbidden)
    monkeypatch.setattr(cr, "model_spec", forbidden)
    with pytest.raises(ValueError, match="Candidate source drift"):
        hr.main(["--registration", str(registration), "--frozen-dir", str(tmp_path / "frozen"),
                 "--out", str(tmp_path / "out.jsonl")])


def test_invalid_cache_samples_are_frozen_not_replaced_or_retried(registered, monkeypatch, tmp_path):
    items, reg, registration, manifest = registered
    selected = items[60:62]
    calls = []

    def missing(item, embedder):
        calls.append(item["question_id"])
        return {"qid": item["question_id"], "category": item["question_type"], "status": "invalid",
                "cache_hits": 0, "cache_failures": [{"cache_key": "missing", "reason": "not_cached"}],
                "used_cache_keys": [], "error_class": "CacheUnavailable"}

    monkeypatch.setattr(cr, "freeze_item", missing)
    frozen_dir = tmp_path / "frozen"
    first = hr.run_samples(selected, object(), manifest, reg, registration, frozen_dir, tmp_path / "first.jsonl")
    second = hr.run_samples(selected, object(), manifest, reg, registration, frozen_dir, tmp_path / "second.jsonl")
    assert calls == [i["question_id"] for i in selected]
    assert all(row["status"] == "invalid" and row["cache_failures"] for row in first + second)
    assert len(list(frozen_dir.glob("*.json"))) == 2
    assert cr.summarize(first)["valid"] == 0


def test_pure_replay_cli_never_loads_data_or_models_and_rejects_wrong_qid(registered, monkeypatch, tmp_path):
    items, reg, registration, manifest = registered
    frozen_dir = tmp_path / "frozen"
    cr.atomic_json(frozen_dir / "manifest.json", manifest)
    first, second = items[60:62]
    good = hr.seal_sample(valid_frozen(first["question_id"], first["question_type"]), manifest)
    wrong = hr.seal_sample(valid_frozen("other-qid", second["question_type"]), manifest)
    cr.atomic_json(cr.frozen_path(frozen_dir, first["question_id"]), good)
    cr.atomic_json(cr.frozen_path(frozen_dir, second["question_id"]), wrong)

    def forbidden(*a, **kw):
        raise AssertionError("Pure replay cannot load dataset/model or freeze new inputs")

    for name in ("load_dataset", "model_spec", "CachedEmbedder", "freeze_item", "CacheOnlyLLM"):
        monkeypatch.setattr(cr, name, forbidden)
    out = tmp_path / "replay.jsonl"
    assert hr.main(["--registration", str(registration), "--frozen-dir", str(frozen_dir),
                    "--out", str(out), "--replay-only"]) == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows[0]["status"] == "valid"
    assert rows[1]["qid"] == second["question_id"] and rows[1]["status"] == "invalid"
    assert rows[1]["error_class"] == "FrozenSampleIdentityError"
    assert rows[-1]["valid"] == 1 and rows[-1]["invalid"] == 409
    assert rows[-1]["measurement_scope"] == hr.SCOPE
    assert len(list(frozen_dir.glob("*.json"))) == 3  # replay does not fabricate missing freezes
    assert "Synthetic fact" not in out.read_text()


@pytest.mark.parametrize("field", ["qid_sha256", "manifest_id", "blocks"])
def test_frozen_identity_and_payload_hash_binding(registered, field):
    _, _, _, manifest = registered
    sample = hr.seal_sample(valid_frozen("q060", cr.CATEGORIES[0]), manifest)
    sample[field] = ["changed"] if field == "blocks" else "changed"
    with pytest.raises(cr.FrozenSampleIdentityError):
        hr.validate_sample(sample, "q060", manifest)


def test_runner_source_drift_rejected(registered):
    _, reg, registration, manifest = registered
    body = {k: v for k, v in manifest.items() if k != "manifest_id"}
    body["runner_source"] = {"path": hr.RUNNER_PATH, "sha256": "changed"}
    changed = cr.seal_manifest(body)
    with pytest.raises(ValueError, match="runner source drift"):
        hr.validate_runtime(changed, registration, reg)
