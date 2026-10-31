import json
from dataclasses import asdict

import pytest

from engram import Memory
from engram.config import Config
from engram.types import Episode
from eval import context_replay as cr


def frozen_sample():
    ep = Episode(content="user: " + "durable raw evidence " * 4000,
                 summary="The complete source summary.", session_id="source", event_time=1,
                 metadata={"date": "1970-01-01"})
    raw = f"[1970-01-01] (session: source)\n{ep.content}"
    return {"schema": 1, "qid": "synthetic", "category": "multi-session", "status": "frozen",
            "blocks": ["FACTS (current, dated):\nA small fact.",
                       "RELEVANT CONVERSATIONS (full detail):\n" + raw],
            "detail_eps": [cr.episode_payload(ep)], "need": None,
            "summary_refs": [{"session_id": "source", "summary": ep.summary, "date": "1970-01-01"}],
            "gold_session_ids": ["source"], "reference_answer": "complete source summary",
            "config": asdict(Config()), "cache_hits": 1, "cache_failures": [],
            "used_cache_keys": [{"key": "synthetic", "reply_sha256": "synthetic"}]}


def test_selector_is_hash_stratified_and_does_not_read_answers():
    rows = [{"question_id": f"{category}-{i}", "question_type": category,
             "answer_session_ids": ["source"], "answer": "changed", "question": "changed"}
            for category in cr.CATEGORIES for i in range(12)]
    chosen = cr.select_items(rows)
    assert len(chosen) == 60
    expected = [i["question_id"] for i in chosen]
    for row in rows:
        del row["answer"]
        del row["question"]
    assert [i["question_id"] for i in cr.select_items(list(reversed(rows)))] == expected
    for category in cr.CATEGORIES:
        ids = [i["question_id"] for i in chosen if i["question_type"] == category]
        assert ids == sorted(ids, key=lambda q: cr.text_hash(cr.SELECTOR + q))
    assert not cr.select_items([{"question_id": "q_abs", "question_type": cr.CATEGORIES[0],
                                 "answer_session_ids": ["source"]}])


def test_cache_only_reads_exact_existing_key_and_never_creates_missing_directory(tmp_path):
    absent = tmp_path / "absent"
    llm = cr.CacheOnlyLLM(absent)
    with pytest.raises(cr.CacheUnavailable):
        llm.complete("request", system="instruction")
    assert not absent.exists()
    assert llm.failures and llm.hits == 0
    cache = tmp_path / "cache"
    cache.mkdir()
    llm = cr.CacheOnlyLLM(cache)
    key = llm.cache._key("request", {"system": "instruction"})
    (cache / key).write_text("cached reply")
    assert llm.complete("request", system="instruction") == "cached reply"
    assert llm.receipt()["used_cache_keys"] == [{"key": key, "reply_sha256": cr.text_hash("cached reply")}]
    assert sorted(p.name for p in cache.iterdir()) == [key]


@pytest.mark.parametrize("payload", [b"", b"  ", b"\xff\xfe"])
def test_empty_or_unreadable_cache_is_invalid(tmp_path, payload):
    llm = cr.CacheOnlyLLM(tmp_path)
    (tmp_path / llm.cache._key("request", {})).write_bytes(payload)
    with pytest.raises(cr.CacheUnavailable):
        llm.complete("request")
    assert len(llm.failures) == 1


def test_swallowed_cache_miss_invalidates_entire_freeze(tmp_path):
    class FakeSystem:
        def __init__(self, rig):
            self.rig = rig

        def _mem_for(self, item):
            try:
                self.rig.extractor_llm.complete("missing")
            except cr.CacheUnavailable:
                pass  # production fallbacks must not masquerade as cache replay success
            mem = Memory.__new__(Memory)
            mem.config = Config()
            mem.episodes_doc = {}
            mem.lean_context = lambda *a, **kw: mem._pack_lean_blocks(["synthetic"], 1, None, [])
            return mem

    result = cr.freeze_item({"question_id": "q", "question_type": "multi-session",
                             "question": "synthetic", "answer_session_ids": ["s"]},
                            None, cr.CacheOnlyLLM(tmp_path), FakeSystem)
    assert result["status"] == "invalid"
    assert result["cache_failures"]
    assert "blocks" not in result
    with pytest.raises(ValueError):
        cr.replay_frozen(result)


def test_both_arms_receive_identical_frozen_inputs_and_budgets():
    frozen = frozen_sample()
    before = json.dumps(frozen, sort_keys=True)
    calls = []

    def pack(mem, blocks, budget, need, eps, *, default_budget):
        calls.append((mem.config.packing_summary_fallback, list(blocks), budget,
                      default_budget, [asdict(ep) for ep in eps]))
        return "\n\n".join(blocks)[:budget]

    result = cr.replay_frozen(frozen, pack)
    assert json.dumps(frozen, sort_keys=True) == before
    for index, budget in enumerate(cr.BUDGETS):
        baseline, candidate = calls[index * 2:index * 2 + 2]
        assert baseline[0] is False and candidate[0] is True
        assert baseline[1:] == candidate[1:]
        assert baseline[2:4] == (budget, budget == 60000)
        assert result["budgets"][str(budget)]["baseline"]["context_chars"] <= budget


def test_replay_needs_no_embedding_retrieval_or_llm(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Replay must not build/query memory or call models")

    monkeypatch.setattr(Memory, "__init__", forbidden)
    monkeypatch.setattr(Memory, "lean_context", forbidden)
    monkeypatch.setattr(cr, "model_spec", forbidden)
    monkeypatch.setattr(cr, "load_dataset", forbidden)
    monkeypatch.setattr(cr.CacheOnlyLLM, "complete", forbidden)
    result = cr.replay_frozen(frozen_sample())
    assert result["status"] == "valid"
    assert result["assembled"]["complete_raw_sessions"] == 1
    assert result["budgets"]["16000"]["baseline"]["complete_raw_sessions"] == 0
    assert result["budgets"]["16000"]["candidate"]["complete_summary_sessions"] == 1


def test_header_or_partial_summary_does_not_count_as_complete():
    frozen = frozen_sample()
    context = "[1970-01-01] (session: source)\nuser: durable\n- [1970-01-01] The complete"
    metrics = cr.context_metrics(context, frozen)
    assert metrics["complete_raw_sessions"] == metrics["complete_summary_sessions"] == 0
    frozen["reference_answer"] = "12345"
    assert cr.context_metrics(context, frozen)["literal_reference_diagnostic_not_qa"] == {
        "eligible": False, "retained": None}


def test_result_contains_no_raw_question_or_reference_answer_and_denominators_separate():
    frozen = frozen_sample()
    result = cr.replay_frozen(frozen)
    encoded = json.dumps(result)
    assert frozen["reference_answer"] not in encoded
    assert frozen["detail_eps"][0]["content"] not in encoded
    summary = cr.summarize([result, {"qid": "invalid", "status": "invalid"}])
    assert (summary["selected"], summary["valid"], summary["invalid"]) == (2, 1, 1)
    assert summary["budgets"]["60000"]["baseline"]["valid_denominator"] == 1


def test_replay_only_cli_reads_frozen_files_without_dataset_or_model(tmp_path, monkeypatch):
    frozen_dir = tmp_path / "frozen"
    frozen_dir.mkdir()
    frozen = frozen_sample()
    manifest = cr.seal_manifest({"selected_qids": ["synthetic", "broken"]})
    cr.atomic_json(frozen_dir / "manifest.json", manifest)
    frozen["manifest_id"] = manifest["manifest_id"]
    cr.atomic_json(cr.frozen_path(frozen_dir, "synthetic"), frozen)
    cr.frozen_path(frozen_dir, "broken").write_text("{broken")

    def forbidden(*args, **kwargs):
        raise AssertionError("No dataset, embedding or LLM during frozen replay")

    monkeypatch.setattr(cr, "load_dataset", forbidden)
    monkeypatch.setattr(cr, "model_spec", forbidden)
    monkeypatch.setattr(cr, "CachedEmbedder", forbidden)
    monkeypatch.setattr(cr, "CacheOnlyLLM", forbidden)
    out = tmp_path / "results.jsonl"
    assert cr.main(["--replay-only", "--frozen-dir", str(frozen_dir), "--out", str(out)]) == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows[0]["status"] == "valid"
    assert rows[1]["status"] == "invalid"
    assert rows[2]["valid"] == rows[2]["invalid"] == 1
    assert frozen["reference_answer"] not in out.read_text()


@pytest.mark.parametrize("replay_only", [False, True])
def test_source_drift_rejects_existing_frozen_directory_before_loading_models(tmp_path, monkeypatch, replay_only):
    frozen_dir = tmp_path / "frozen"
    manifest = cr.seal_manifest({"selected_qids": ["synthetic"]})
    cr.atomic_json(frozen_dir / "manifest.json", manifest)
    fingerprints = cr.source_fingerprints()
    assert {"engram/memory.py", "engram/config.py", "engram/retrieve/evidence.py",
            "eval/context_replay.py", "eval/bench.py"} <= set(fingerprints)
    fingerprints["engram/memory.py"] = "changed-source"
    monkeypatch.setattr(cr, "source_fingerprints", lambda: fingerprints)

    def forbidden(*args, **kwargs):
        raise AssertionError("Reject drift before reading dataset or loading a model")

    monkeypatch.setattr(cr, "load_dataset", forbidden)
    monkeypatch.setattr(cr, "model_spec", forbidden)
    out = tmp_path / "result.jsonl"
    args = ["--frozen-dir", str(frozen_dir), "--out", str(out)]
    with pytest.raises(ValueError, match="source fingerprint drift"):
        cr.main(args + (["--replay-only"] if replay_only else []))
    assert not out.exists()


@pytest.mark.parametrize("field", ["qid", "manifest_id"])
def test_wrong_frozen_identity_is_invalid_not_replayed(tmp_path, monkeypatch, field):
    frozen_dir = tmp_path / "frozen"
    manifest = cr.seal_manifest({"selected_qids": ["synthetic"]})
    cr.atomic_json(frozen_dir / "manifest.json", manifest)
    frozen = frozen_sample()
    frozen["manifest_id"] = manifest["manifest_id"]
    frozen[field] = "different-identity"
    cr.atomic_json(cr.frozen_path(frozen_dir, "synthetic"), frozen)

    def forbidden(*args, **kwargs):
        raise AssertionError("Mismatched samples must not reach packing")

    monkeypatch.setattr(cr, "replay_frozen", forbidden)
    out = tmp_path / "result.jsonl"
    cr.main(["--replay-only", "--frozen-dir", str(frozen_dir), "--out", str(out)])
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows[0]["qid"] == "synthetic"
    assert rows[0]["status"] == "invalid"
    assert rows[0]["error_class"] == "FrozenSampleIdentityError"
    assert rows[1]["valid"] == 0 and rows[1]["invalid"] == 1
