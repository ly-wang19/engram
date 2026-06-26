from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _metrics(path: Path, system: str) -> dict[str, float | int]:
    scored: list[dict] = []
    errors = 0
    for row in _rows(path):
        res = row.get("sys", {}).get(system)
        if not res:
            continue
        if res.get("err"):
            errors += 1
            continue
        if res.get("ok") is not None:
            scored.append(res)
    return {
        "n": len(scored),
        "errors": errors,
        "accuracy": round(100.0 * sum(bool(r["ok"]) for r in scored) / len(scored), 1),
        "avg_tokens": sum(int(r.get("tok", 0)) for r in scored) // len(scored),
    }


def test_results_md_raw_log_links_exist():
    text = (ROOT / "RESULTS.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\((results/[^)]+\.jsonl)\)", text)
    assert links
    for link in links:
        assert (ROOT / link).exists(), link


def test_results_md_headline_numbers_match_raw_logs():
    lean = _metrics(ROOT / "results/longmemeval_s_engram_lean_v2_final.jsonl", "engram_lean")
    assert lean == {"n": 500, "errors": 0, "accuracy": 83.6, "avg_tokens": 9568}

    baseline = _metrics(ROOT / "results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl", "full_context")
    assert baseline == {"n": 500, "errors": 0, "accuracy": 73.2, "avg_tokens": 79241}

    full = _metrics(ROOT / "results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl", "engram_full")
    assert full == {"n": 499, "errors": 1, "accuracy": 83.4, "avg_tokens": 79541}


def test_public_copy_avoids_unmeasured_scale_or_latency_claims():
    forbidden = (
        "cost stays flat",
        "sub-second",
        "holds up as history grows",
        "历史再长也不崩",
        "<50ms",
        "&lt;50ms",
        "<100ms",
        "&lt;100ms",
        "under 50ms",
        "~50ms",
    )
    for rel in ("README.md", "README.zh-CN.md", "RESULTS.md", "docs/index.html"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        lowered = text.lower()
        for phrase in forbidden:
            assert phrase.lower() not in lowered, f"{phrase!r} found in {rel}"


def test_public_headline_claims_are_traceable_to_results():
    expected_claims = ("83.6", "73.2", "9.6k", "79k")
    for rel in ("README.md", "README.zh-CN.md", "docs/index.html"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for claim in expected_claims:
            assert claim in text, f"{claim!r} missing from {rel}"
        assert "RESULTS.md" in text, f"{rel} must link headline claims back to methodology/raw logs"


def test_report_prints_p50_and_p95_latency_for_committed_logs():
    out = subprocess.check_output(
        [
            sys.executable,
            str(ROOT / "eval/report.py"),
            str(ROOT / "results/longmemeval_s_engram_lean_v2_final.jsonl"),
        ],
        text=True,
    )
    assert "p50 latency ms" in out
    assert "p95 latency ms" in out
