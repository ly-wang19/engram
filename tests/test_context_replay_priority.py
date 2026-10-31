import json
from copy import deepcopy
from dataclasses import asdict

import pytest

from engram import Memory, Config
from engram.types import Episode
from engram.util import fmt_date
from eval import context_replay as cr
from eval.context_replay_priority import main, priority_pack, verified_inputs


def fixture():
    mem = Memory.__new__(Memory)
    mem.config = Config(packing_summary_fallback=True)
    ep = Episode("Background chatter. " * 100, session_id="detail", event_time=1700000000,
                 summary="RECOVERED SOURCE.")
    raw = ("RELEVANT CONVERSATIONS (full detail):\n"
           f"[{fmt_date(ep.event_time)}] (session: {ep.session_id})\n{ep.content}")
    blocks = ["SESSION SUMMARIES (relevant, chronological):\n" + "Existing summary. " * 100, raw]
    return mem, blocks, [ep]


def test_prepend_recovers_the_fallback_that_append_truncates_again():
    mem, blocks, eps = fixture()
    appended = Memory._pack_lean_blocks(mem, blocks, 400, None, eps)
    assert "RECOVERED SOURCE." not in appended
    priority = priority_pack(mem, blocks, 400, None, eps)
    assert "RECOVERED SOURCE." in priority
    assert len(priority) <= 400
    assert priority.index("SESSION SUMMARIES") < priority.index("RELEVANT CONVERSATIONS")


@pytest.mark.parametrize("budgeting", [False, True])
@pytest.mark.parametrize("default_budget", [False, True])
@pytest.mark.parametrize("budget", [0, 1, 80, 400, 10000])
def test_fixed_inputs_budget_and_flag_restoration(budgeting, default_budget, budget):
    mem, blocks, eps = fixture()
    mem.config.evidence_budgeting = budgeting
    before_blocks, before_eps, before_config = deepcopy(blocks), deepcopy(eps), asdict(mem.config)
    result = priority_pack(mem, blocks, budget, None, eps, default_budget=default_budget)
    assert len(result) <= budget
    assert blocks == before_blocks and eps == before_eps and asdict(mem.config) == before_config


def test_disabled_candidate_is_exact_baseline_and_complete_raw_needs_no_fallback():
    mem, blocks, eps = fixture()
    mem.config.packing_summary_fallback = False
    assert priority_pack(mem, blocks, 400, None, eps) == Memory._pack_lean_blocks(mem, blocks, 400, None, eps)
    assert mem.config.packing_summary_fallback is False
    mem.config.packing_summary_fallback = True
    assert priority_pack(mem, blocks, 10000, None, eps) == "\n\n".join(blocks)
    assert "RECOVERED SOURCE." not in priority_pack(mem, blocks, 10000, None, eps)


def test_flag_is_restored_when_baseline_raises(monkeypatch):
    mem, blocks, eps = fixture()

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic packing failure")

    monkeypatch.setattr(Memory, "_pack_lean_blocks", broken)
    with pytest.raises(RuntimeError):
        priority_pack(mem, blocks, 400, None, eps)
    assert mem.config.packing_summary_fallback is True


def test_packer_has_no_gold_or_model_dependencies_and_missing_summary_is_not_invented():
    mem, blocks, eps = fixture()
    # Memory.__new__ has no retriever, embedder, llm, or stores. Metadata must not steer ranking.
    first = priority_pack(mem, blocks, 400, None, eps)
    eps[0].metadata.update(gold=True, answer="unrelated evaluation metadata")
    assert priority_pack(mem, blocks, 400, None, eps) == first
    eps[0].summary = ""
    result = priority_pack(mem, blocks, 400, None, eps)
    assert "RECOVERED SOURCE." not in result


def frozen_fixture(tmp_path):
    directory = tmp_path / "frozen"
    directory.mkdir()
    parent = cr.seal_manifest({"schema": 2, "selected_qids": ["synthetic"], "budgets": list(cr.BUDGETS)})
    (directory / "manifest.json").write_text(json.dumps(parent))
    mem, blocks, eps = fixture()
    frozen = {"schema": 1, "qid": "synthetic", "category": "multi-session", "status": "frozen",
              "manifest_id": parent["manifest_id"], "blocks": blocks, "need": None,
              "detail_eps": [asdict(e) for e in eps], "config": asdict(mem.config),
              "summary_refs": [{"session_id": eps[0].session_id, "summary": eps[0].summary,
                                "date": fmt_date(eps[0].event_time)}],
              "gold_session_ids": [eps[0].session_id], "reference_answer": "RECOVERED SOURCE.",
              "cache_hits": 0, "cache_failures": [], "used_cache_keys": []}
    path = cr.frozen_path(directory, "synthetic")
    path.write_text(json.dumps(frozen))
    row = cr.replay_frozen(frozen)
    row["manifest_id"] = parent["manifest_id"]
    run = tmp_path / "parent.jsonl"
    run.write_text(json.dumps(row) + "\n" + json.dumps(cr.summarize([row])) + "\n")
    run.with_suffix(run.suffix + ".manifest.json").write_text(json.dumps(parent))
    return directory, run, path


def test_cli_replays_only_verified_frozen_data_and_refuses_overwrite(tmp_path, monkeypatch):
    directory, run, _ = frozen_fixture(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("No dataset or model may be loaded in replay")

    monkeypatch.setattr(cr, "load_dataset", forbidden)
    monkeypatch.setattr(cr, "model_spec", forbidden)
    out = tmp_path / "new.jsonl"
    args = ["--frozen-dir", str(directory), "--run", str(run), "--out", str(out)]
    assert main(args) == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows[0]["status"] == "valid" and set(rows[0]["budgets"]) == {"60000", "32000", "16000"}
    manifest = json.loads(out.with_suffix(out.suffix + ".manifest.json").read_text())
    assert manifest["parent_manifest_id"] == rows[0]["parent_manifest_id"]
    assert manifest["candidate_source"]["sha256"]
    cr.validate_manifest(manifest)
    with pytest.raises(ValueError, match="choose a new output"):
        main(args)


def test_changed_frozen_content_is_rejected_against_parent_run(tmp_path):
    directory, run, path = frozen_fixture(tmp_path)
    frozen = json.loads(path.read_text())
    frozen["blocks"][0] += "changed"
    path.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match="identity/content differs"):
        verified_inputs(directory, run)
