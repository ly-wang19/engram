from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from eval.validate_results import validate_bench_log


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
    latencies = sorted(float(r.get("lat", 0.0)) for r in scored)

    def percentile(p: float) -> int:
        idx = min(len(latencies) - 1, int(round((p / 100.0) * (len(latencies) - 1))))
        return round(latencies[idx])

    return {
        "n": len(scored),
        "errors": errors,
        "accuracy": round(100.0 * sum(bool(r["ok"]) for r in scored) / len(scored), 1),
        "avg_tokens": sum(int(r.get("tok", 0)) for r in scored) // len(scored),
        "p50_latency_ms": percentile(50),
        "p95_latency_ms": percentile(95),
    }


def _category_metrics(path: Path, system: str) -> dict[str, tuple[float, int]]:
    by_category: dict[str, list[bool]] = {}
    for row in _rows(path):
        res = row.get("sys", {}).get(system)
        if not res or res.get("err") or res.get("ok") is None:
            continue
        category = "abstention" if str(row.get("qid", "")).endswith("_abs") else row.get("cat", "?")
        by_category.setdefault(category, []).append(bool(res["ok"]))
    return {
        category: (round(100.0 * sum(values) / len(values), 1), len(values))
        for category, values in by_category.items()
    }


def test_results_md_raw_log_links_exist():
    text = (ROOT / "RESULTS.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\((results/[^)]+\.jsonl)\)", text)
    assert links
    for link in links:
        assert (ROOT / link).exists(), link


def test_paper_done_result_logs_are_complete_and_scored():
    text = (ROOT / "paper/EXPERIMENTS_CHECKLIST.md").read_text(encoding="utf-8")
    links = set()
    for line in text.splitlines():
        if "[DONE" in line:
            links.update(re.findall(r"results/[A-Za-z0-9_.-]+\.jsonl", line))
    assert links
    for link in links:
        errors = validate_bench_log(ROOT / link, expected_rows=500, require_complete=True)
        assert errors == []


def test_results_md_raw_logs_have_required_schema():
    text = (ROOT / "RESULTS.md").read_text(encoding="utf-8")
    links = sorted(set(re.findall(r"\]\((results/[^)]+\.jsonl)\)", text)))
    assert links
    for link in links:
        rows = _rows(ROOT / link)
        assert rows, link
        for i, row in enumerate(rows, start=1):
            assert isinstance(row.get("qid"), str) and row["qid"], f"{link}:{i} missing qid"
            assert isinstance(row.get("cat"), str) and row["cat"], f"{link}:{i} missing cat"
            systems = row.get("sys")
            assert isinstance(systems, dict) and systems, f"{link}:{i} missing sys"
            for system, result in systems.items():
                assert isinstance(system, str) and system, f"{link}:{i} has empty system name"
                assert isinstance(result, dict), f"{link}:{i} {system} result must be object"
                assert {"ok", "tok", "lat", "err"} <= set(result), (
                    f"{link}:{i} {system} missing required result keys"
                )
                assert result["ok"] is None or isinstance(result["ok"], bool), f"{link}:{i} {system} bad ok"
                assert isinstance(result["tok"], int) and result["tok"] >= 0, f"{link}:{i} {system} bad tok"
                assert isinstance(result["lat"], (int, float)) and result["lat"] >= 0, (
                    f"{link}:{i} {system} bad lat"
                )
                assert result["err"] is None or isinstance(result["err"], str), f"{link}:{i} {system} bad err"
                if result["err"] is None and result["ok"] is not None:
                    assert {"pred", "gold"} <= set(result), f"{link}:{i} {system} missing pred/gold"


def test_public_raw_logs_have_unique_and_matching_qids():
    text = (ROOT / "RESULTS.md").read_text(encoding="utf-8")
    links = sorted(set(re.findall(r"\]\((results/[^)]+\.jsonl)\)", text)))
    assert links
    qids_by_link: dict[str, set[str]] = {}
    for link in links:
        rows = _rows(ROOT / link)
        qids = [row["qid"] for row in rows]
        assert len(qids) == len(set(qids)), f"{link} contains duplicate qids"
        qids_by_link[link] = set(qids)

    lean_qids = qids_by_link["results/longmemeval_s_engram_lean_v2_final.jsonl"]
    baseline_qids = qids_by_link["results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl"]
    assert len(lean_qids) == 500
    assert lean_qids == baseline_qids


def test_public_raw_logs_have_matching_qid_categories():
    lean_rows = _rows(ROOT / "results/longmemeval_s_engram_lean_v2_final.jsonl")
    baseline_rows = _rows(ROOT / "results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl")
    lean_categories = {row["qid"]: row["cat"] for row in lean_rows}
    baseline_categories = {row["qid"]: row["cat"] for row in baseline_rows}

    assert lean_categories == baseline_categories


def test_public_raw_logs_have_expected_category_distribution():
    expected = {
        "single-session-assistant": 56,
        "single-session-user": 64,
        "knowledge-update": 72,
        "abstention": 30,
        "temporal-reasoning": 127,
        "multi-session": 121,
        "single-session-preference": 30,
    }
    for rel in (
        "results/longmemeval_s_engram_lean_v2_final.jsonl",
        "results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl",
    ):
        rows = _rows(ROOT / rel)
        counts = Counter(
            "abstention" if str(row.get("qid", "")).endswith("_abs") else row["cat"]
            for row in rows
        )
        assert dict(counts) == expected
        assert sum(counts.values()) == 500


def test_public_raw_logs_have_matching_gold_by_qid():
    paths = (
        ROOT / "results/longmemeval_s_engram_lean_v2_final.jsonl",
        ROOT / "results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl",
    )
    gold_by_qid: dict[str, str] = {}
    for path in paths:
        for row in _rows(path):
            qid = row["qid"]
            for system, result in row["sys"].items():
                if result.get("err") is not None or result.get("ok") is None:
                    continue
                assert "gold" in result, f"{path.name}:{qid}:{system} missing gold"
                existing = gold_by_qid.setdefault(qid, result["gold"])
                assert existing == result["gold"], f"{qid} gold mismatch for {system}"

    assert len(gold_by_qid) == 500


def test_results_md_headline_numbers_match_raw_logs():
    lean = _metrics(ROOT / "results/longmemeval_s_engram_lean_v2_final.jsonl", "engram_lean")
    assert lean == {
        "n": 500,
        "errors": 0,
        "accuracy": 83.6,
        "avg_tokens": 9568,
        "p50_latency_ms": 60535,
        "p95_latency_ms": 106623,
    }

    baseline = _metrics(ROOT / "results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl", "full_context")
    assert baseline == {
        "n": 500,
        "errors": 0,
        "accuracy": 73.2,
        "avg_tokens": 79241,
        "p50_latency_ms": 15603,
        "p95_latency_ms": 64161,
    }

    full = _metrics(ROOT / "results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl", "engram_full")
    assert full == {
        "n": 499,
        "errors": 1,
        "accuracy": 83.4,
        "avg_tokens": 79541,
        "p50_latency_ms": 50520,
        "p95_latency_ms": 101620,
    }

    results = (ROOT / "RESULTS.md").read_text(encoding="utf-8")
    assert "p50 **60.5s** / p95 **106.6s**" in results


def test_results_md_category_numbers_match_raw_logs():
    categories = _category_metrics(
        ROOT / "results/longmemeval_s_engram_lean_v2_final.jsonl",
        "engram_lean",
    )
    expected = {
        "single-session-assistant": (92.9, 56),
        "single-session-user": (87.5, 64),
        "knowledge-update": (87.5, 72),
        "abstention": (86.7, 30),
        "temporal-reasoning": (81.1, 127),
        "multi-session": (79.3, 121),
        "single-session-preference": (73.3, 30),
    }
    assert categories == expected

    public_docs = "\n".join(
        (ROOT / rel).read_text(encoding="utf-8")
        for rel in ("RESULTS.md", "README.md", "README.zh-CN.md")
    )
    for score, count in expected.values():
        assert f"{score:.1f}%" in public_docs
        assert str(count) in public_docs


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
    for rel in ("README.md", "README.zh-CN.md", "RESULTS.md", "docs/index.html", "demo/index.html"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        lowered = text.lower()
        for phrase in forbidden:
            assert phrase.lower() not in lowered, f"{phrase!r} found in {rel}"


def test_public_headline_claims_are_traceable_to_results():
    expected_claims = ("83.6", "73.2", "9.6k", "79k")
    for rel in ("README.md", "README.zh-CN.md", "docs/index.html", "demo/index.html"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for claim in expected_claims:
            assert claim in text, f"{claim!r} missing from {rel}"
        assert "RESULTS.md" in text, f"{rel} must link headline claims back to methodology/raw logs"


def test_contributor_headline_claims_match_results():
    expected_claims = ("83.6", "73.2", "+10.4", "9.6k", "79k")
    stale_claims = ("74.8", "+8.8")
    for rel in ("AGENTS.md", "CLAUDE.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for claim in expected_claims:
            assert claim in text, f"{claim!r} missing from {rel}"
        for claim in stale_claims:
            assert claim not in text, f"stale claim {claim!r} found in {rel}"


def test_public_derived_headline_claims_match_raw_logs():
    lean = _metrics(ROOT / "results/longmemeval_s_engram_lean_v2_final.jsonl", "engram_lean")
    baseline = _metrics(ROOT / "results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl", "full_context")
    accuracy_delta = round(float(lean["accuracy"]) - float(baseline["accuracy"]), 1)
    token_ratio = round(float(baseline["avg_tokens"]) / float(lean["avg_tokens"]))

    assert accuracy_delta == 10.4
    assert token_ratio == 8

    docs = ("README.md", "README.zh-CN.md", "RESULTS.md", "docs/index.html", "demo/index.html")
    for rel in docs:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "+10.4" in text, f"derived accuracy delta missing from {rel}"
        compact = text.replace(" ", "")
        assert (
            "8×" in text
            or "8x" in text
            or "8倍" in compact
        ), f"derived token ratio missing from {rel}"


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


def test_report_prints_scored_denominators_for_errored_systems():
    out = subprocess.check_output(
        [
            sys.executable,
            str(ROOT / "eval/report.py"),
            str(ROOT / "results/longmemeval_s_volcano_doubao_deepseekjudge.jsonl"),
        ],
        text=True,
    )
    assert "scored items" in out
    assert "499/500" in out
    assert "500/500" in out


def test_report_prints_every_requested_log():
    out = subprocess.check_output(
        [
            sys.executable,
            str(ROOT / "eval/report.py"),
            str(ROOT / "results/headline_500.jsonl"),
            str(ROOT / "results/bb_flash.jsonl"),
        ],
        text=True,
    )
    assert str(ROOT / "results/headline_500.jsonl") in out
    assert str(ROOT / "results/bb_flash.jsonl") in out


def test_compute_stats_prints_committed_backbone_summary():
    out = subprocess.check_output(
        [
            sys.executable,
            str(ROOT / "paper/compute_stats.py"),
            "--bootstrap-samples",
            "100",
        ],
        text=True,
    )

    assert "committed multi-backbone headline runs" in out
    assert "doubao-pro" in out
    assert "doubao-flash" in out
    assert "lean-full gap range across committed backbones: +3.0..+12.2 points" in out
