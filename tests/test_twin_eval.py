from __future__ import annotations

import json
import re

from eval.twin_eval import evaluate, write_jsonl


EXPECTED_SCENARIOS = [
    "auth.default_deny",
    "scope.segment_match",
    "scope.prefix_bypass",
    "scope.dotdot_bypass",
    "scope.encoded_bypass",
    "scope.backslash_bypass",
    "scope.wildcard_bypass",
    "confirmation.external_write_gate",
    "confirmation.owner_confirmed",
    "confirmation.authorization_never_executes",
    "grant.expired_deny",
    "grant.revoked_deny",
    "contract.versioned_revision",
    "audit.persistence_roundtrip",
    "erasure.provenance_cascade",
    "erasure.unrelated_source_preserved",
]


def test_twin_eval_passes_every_traceable_scenario() -> None:
    rows, summary = evaluate()

    assert [row["id"] for row in rows] == EXPECTED_SCENARIOS
    assert all(row["ok"] for row in rows)
    assert summary["passed"] == len(EXPECTED_SCENARIOS)
    assert summary["total"] == len(EXPECTED_SCENARIOS)
    assert summary["pass_rate"] == 100.0
    assert summary["scenario_ids"] == EXPECTED_SCENARIOS
    assert summary["failures"] == []
    assert sum(item["total"] for item in summary["per_category"].values()) == summary["total"]


def test_twin_eval_output_is_deterministic_and_contains_no_runtime_ids() -> None:
    first_rows, first_summary = evaluate()
    second_rows, second_summary = evaluate()

    assert first_rows == second_rows
    assert first_summary == second_summary
    encoded = json.dumps([*first_rows, first_summary], sort_keys=True)
    assert re.search(r"\b(?:grant|decision|episode)_[0-9a-f]{12}\b", encoded) is None


def test_twin_eval_jsonl_has_scenarios_and_final_summary(tmp_path) -> None:
    rows, summary = evaluate()
    path = tmp_path / "twin-eval.jsonl"
    write_jsonl(str(path), rows, summary)

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == len(EXPECTED_SCENARIOS) + 1
    assert all(record["type"] == "scenario" for record in records[:-1])
    assert records[-1]["type"] == "summary"
    assert records[-1]["scenario_ids"] == EXPECTED_SCENARIOS
    assert records[-1]["evidence_scope"].endswith("not public benchmark evidence")
