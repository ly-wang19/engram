from __future__ import annotations

import json

from eval.report import format_bench_report
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


def test_validate_bench_log_can_require_only_named_systems(tmp_path):
    path = tmp_path / "mixed.jsonl"
    rows = [
        _row("q1", system="full_context"),
        _row("q2", ok=False, system="full_context"),
    ]
    for row in rows:
        row["sys"]["engram_full"] = {
            "ok": None,
            "tok": 0,
            "lat": 1.0,
            "err": "timeout",
        }
    _write_jsonl(path, rows)

    assert validate_bench_log(
        path,
        expected_rows=2,
        require_complete=True,
        required_systems=("full_context",),
    ) == []


def test_validate_bench_log_rejects_missing_required_system(tmp_path):
    path = tmp_path / "missing_system.jsonl"
    _write_jsonl(path, [_row("q1", system="full_context")])

    errors = validate_bench_log(
        path,
        expected_rows=1,
        require_complete=True,
        required_systems=("engram_lean",),
    )

    assert f"{path}:engram_lean: missing from log" in errors


def test_validate_personamem_log_accepts_choice_schema(tmp_path):
    path = tmp_path / "personamem.jsonl"
    _write_jsonl(path, [
        {
            "qid": "p1_1",
            "pref_type": "updated_preference",
            "sys": {
                "engram_lean": {"ok": True, "pick": 0, "tok": 3000, "lat": 12.3},
                "full_context": {"ok": False, "pick": 1, "tok": 18000, "lat": 45.6},
            },
        }
    ])

    assert validate_bench_log(path, expected_rows=1, require_complete=True, schema="personamem") == []


def test_validate_personamem_log_rejects_missing_category_or_choice(tmp_path):
    path = tmp_path / "bad_personamem.jsonl"
    _write_jsonl(path, [
        {
            "qid": "p1_1",
            "sys": {"engram_lean": {"ok": True, "tok": 3000, "lat": 12.3}},
        },
        {
            "qid": "p1_2",
            "pref_type": "neutral_preferences",
            "sys": {"engram_lean": {"ok": True, "pick": 7, "tok": 3000, "lat": 12.3}},
        },
    ])

    errors = validate_bench_log(path, expected_rows=2, require_complete=True, schema="personamem")

    assert f"{path}:1: missing pref_type" in errors
    assert f"{path}:1: engram_lean missing keys ['pick']" in errors
    assert f"{path}:2: engram_lean pick must be an int in [-1, 3]" in errors


def test_report_uses_personamem_pref_type_as_category(tmp_path):
    path = tmp_path / "personamem.jsonl"
    rows = [
        {
            "qid": "p1_1",
            "pref_type": "updated_preference",
            "sys": {"engram_lean": {"ok": True, "pick": 0, "tok": 3000, "lat": 12.3}},
        }
    ]
    _write_jsonl(path, rows)

    report = format_bench_report(str(path), rows)

    assert "updated_preference" in report
    assert "?                         100.0%" not in report
    assert "Public LongMemEval_S SOTA" not in report
    assert "PersonaMem-v2 is a multiple-choice personalization benchmark" in report


def test_report_keeps_long_categories_readable(tmp_path):
    path = tmp_path / "personamem.jsonl"
    rows = [
        {
            "qid": "p1_1",
            "pref_type": "health_and_medical_conditions",
            "sys": {"engram_lean": {"ok": True, "pick": 0, "tok": 3000, "lat": 12.3}},
        }
    ]

    report = format_bench_report(str(path), rows)

    assert "health_and_medical_conditions  100.0% (1)" in report
