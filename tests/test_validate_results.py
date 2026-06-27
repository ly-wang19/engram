from __future__ import annotations

import json

from eval.validate_results import validate_bench_log


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _row(qid: str, *, ok=True, err=None, include_pred_gold=True, system="engram_lean"):
    result = {"ok": ok, "tok": 123, "lat": 45.6, "err": err}
    if include_pred_gold:
        result.update({"pred": "A", "gold": "A"})
    return {"qid": qid, "cat": "single-session-user", "sys": {system: result}}


def test_validate_bench_log_accepts_complete_scored_log(tmp_path):
    path = tmp_path / "complete.jsonl"
    _write_jsonl(path, [_row("q1"), _row("q2", ok=False)])

    assert validate_bench_log(path, expected_rows=2, require_complete=True) == []


def test_validate_bench_log_rejects_partial_or_unscored_log(tmp_path):
    path = tmp_path / "partial.jsonl"
    _write_jsonl(path, [
        _row("q1"),
        _row("q2", ok=None, include_pred_gold=False),
        _row("q2", err="timeout", include_pred_gold=False),
    ])

    errors = validate_bench_log(path, expected_rows=2, require_complete=True)

    assert f"{path}: expected 2 rows, got 3" in errors
    assert f"{path}: duplicate qids" in errors
    assert f"{path}:engram_lean: has 1 error row(s)" in errors
    assert f"{path}:engram_lean: scored 1/3 rows" in errors
