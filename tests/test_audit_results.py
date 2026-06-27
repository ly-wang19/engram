from __future__ import annotations

import json

from eval.audit_results import audit_log, format_audit, infer_schema


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _bench_row(qid: str, *, ok=True, err=None):
    return {
        "qid": qid,
        "cat": "single-session-user",
        "sys": {
            "engram_lean": {
                "ok": ok,
                "tok": 123,
                "lat": 45.6,
                "err": err,
                "pred": "A",
                "gold": "A",
            }
        },
    }


def test_audit_log_marks_complete_bench_log(tmp_path):
    path = tmp_path / "complete.jsonl"
    _write_jsonl(path, [_bench_row("q1"), _bench_row("q2", ok=False)])

    audit = audit_log(path, bench_rows=2)

    assert audit.status == "complete"
    assert audit.rows == 2
    assert audit.schema == "bench"
    assert audit.systems[0].scored == 2
    assert audit.validation_errors == ()


def test_audit_log_marks_partial_and_errored_runs(tmp_path):
    partial = tmp_path / "partial.jsonl"
    _write_jsonl(partial, [_bench_row("q1")])
    errored = tmp_path / "errored.jsonl"
    _write_jsonl(errored, [_bench_row("q1"), _bench_row("q2", ok=None, err="timeout")])

    partial_audit = audit_log(partial, bench_rows=2)
    errored_audit = audit_log(errored, bench_rows=2)

    assert partial_audit.status == "incomplete"
    assert any("expected 2 rows, got 1" in error for error in partial_audit.validation_errors)
    assert errored_audit.status == "invalid"
    assert errored_audit.systems[0].errors == 1
    assert any("has 1 error row" in error for error in errored_audit.validation_errors)


def test_audit_log_infers_personamem_schema(tmp_path):
    path = tmp_path / "personamem.jsonl"
    rows = [
        {
            "qid": "p1_1",
            "pref_type": "updated_preference",
            "sys": {"engram_lean": {"ok": True, "pick": 0, "tok": 3000, "lat": 12.3}},
        }
    ]
    _write_jsonl(path, rows)

    assert infer_schema(rows) == "personamem"
    audit = audit_log(path)

    assert audit.status == "complete"
    assert audit.schema == "personamem"
    assert audit.systems[0].scored == 1


def test_format_audit_includes_validation_errors(tmp_path):
    path = tmp_path / "partial.jsonl"
    _write_jsonl(path, [_bench_row("q1")])

    text = format_audit([audit_log(path, bench_rows=2)])

    assert str(path) in text
    assert "incomplete" in text
    assert "expected 2 rows, got 1" in text
