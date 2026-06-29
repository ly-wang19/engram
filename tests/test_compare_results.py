from __future__ import annotations

import json

from eval.compare import format_pair_compare


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _row(qid: str, cat: str, ok: bool, *, err: str | None = None):
    return {
        "qid": qid,
        "cat": cat,
        "sys": {
            "engram_lean": {
                "ok": ok,
                "tok": 100,
                "lat": 10.0,
                "err": err,
                "pred": "A",
                "gold": "A",
            }
        },
    }


def test_compare_reports_shared_accuracy_disagreement_and_oracle(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    _write_jsonl(
        a,
        [
            _row("q1", "single-hop", True),
            _row("q2", "temporal", False),
            _row("q3", "temporal", True),
            _row("q4", "multi-hop", False, err="timeout"),
        ],
    )
    _write_jsonl(
        b,
        [
            _row("q1", "single-hop", True),
            _row("q2", "temporal", True),
            _row("q3", "temporal", False),
            _row("q5", "single-hop", True),
        ],
    )

    report = format_pair_compare([str(a), str(b)], "engram_lean", per_category=True)

    assert "shared scored qids: 3" in report
    assert "OVERALL (shared)" in report
    assert "66.7% (3)" in report
    assert "both right:  1" in report
    assert "both wrong:  0" in report
    assert "a only: 1" in report
    assert "b only: 1" in report
    assert "oracle (per-q max): 3/3 = 100.0%" in report
    assert "per-category disagreement" in report


def test_compare_requires_unambiguous_shared_system(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    row = {
        "qid": "q1",
        "cat": "single-hop",
        "sys": {
            "engram_lean": {"ok": True},
            "full_context": {"ok": False},
        },
    }
    _write_jsonl(a, [row])
    _write_jsonl(b, [row])

    report = format_pair_compare([str(a), str(b)], None, per_category=False)

    assert "Multiple system names are shared" in report
    assert "Pass --system" in report
