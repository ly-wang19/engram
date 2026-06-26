from __future__ import annotations

import json

import pytest

from eval.harness import evaluate, run_engram, write_jsonl
from eval.report import format_synthetic_report, load
from eval.synthetic import ITEMS


def test_synthetic_harness_durable_storage_smoke():
    item = next(it for it in ITEMS if it.id == "tmp2")
    ok, tokens, latency = run_engram(item, storage="durable")
    assert ok is True
    assert tokens > 0
    assert latency >= 0.0


def test_synthetic_harness_lancedb_storage_smoke():
    pytest.importorskip("lancedb")
    item = next(it for it in ITEMS if it.id == "tmp2")
    ok, tokens, latency = run_engram(item, storage="lancedb")
    assert ok is True
    assert tokens > 0
    assert latency >= 0.0


def test_synthetic_harness_jsonl_raw_log_contains_items_and_summary(tmp_path):
    rows, summary = evaluate(storage="memory")
    path = tmp_path / "harness.jsonl"
    write_jsonl(str(path), rows, summary)

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == len(ITEMS) + 1
    assert records[-1]["type"] == "summary"
    assert records[-1]["accuracy"] == 100.0
    assert records[-1]["storage"] == "memory"
    first = records[0]
    assert {"id", "category", "question", "gold", "ok", "tokens", "latency_ms", "naive_ok"} <= first.keys()


def test_report_reads_synthetic_harness_jsonl(tmp_path):
    rows, summary = evaluate(storage="memory")
    path = tmp_path / "harness.jsonl"
    write_jsonl(str(path), rows, summary)

    report = format_synthetic_report(str(path), load(str(path)))
    assert "Synthetic offline harness" in report
    assert "OVERALL" in report
    assert "100.0% (9)" in report
    assert "avg context tokens" in report
    assert "p50 latency ms" in report
    assert "p95 latency ms" in report
