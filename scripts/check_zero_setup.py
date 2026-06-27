#!/usr/bin/env python3
"""Run Engram's zero-setup verification path.

This script intentionally uses only the Python standard library. It is the quick
confidence check for contributors before they install optional services or test
dependencies: quickstart, offline eval, evidence-log validation, paper stats, and
stdlib compilation.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_EVIDENCE_LOGS = (
    ROOT / "results/headline_500.jsonl",
    ROOT / "results/bb_flash.jsonl",
)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def run_step(label: str, cmd: list[str]) -> None:
    printable = " ".join(_rel(Path(part)) if part.startswith(str(ROOT)) else part for part in cmd)
    print(f"\n==> {label}", flush=True)
    print(f"$ {printable}", flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Engram's zero-setup smoke checks.")
    parser.add_argument(
        "--skip-paper-stats",
        action="store_true",
        help="skip recomputing committed paper statistics from result logs",
    )
    args = parser.parse_args(argv)

    py = sys.executable
    missing = [path for path in PUBLIC_EVIDENCE_LOGS if not path.exists()]
    if missing:
        for path in missing:
            print(f"missing public evidence log: {_rel(path)}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="engram_zero_setup_") as tmp:
        synthetic_log = Path(tmp) / "synthetic_durable.jsonl"
        run_step("quickstart", [py, "examples/quickstart.py"])
        run_step("offline synthetic eval", [py, "eval/harness.py"])
        run_step(
            "durable smoke eval",
            [py, "eval/harness.py", "--storage", "durable", "--out", str(synthetic_log)],
        )
        run_step("synthetic report", [py, "eval/report.py", str(synthetic_log)])

    evidence = [str(path) for path in PUBLIC_EVIDENCE_LOGS]
    run_step("public evidence audit", [py, "eval/audit_results.py", "--fail-invalid", *evidence])
    run_step(
        "public evidence validation",
        [py, "eval/validate_results.py", "--expected-rows", "500", "--require-complete", *evidence],
    )
    if not args.skip_paper_stats:
        run_step("paper statistics", [py, "paper/compute_stats.py", "--bootstrap-samples", "100"])
    run_step("stdlib compile check", [py, "-m", "compileall", "-q", "engram", "eval", "examples", "tests"])

    print("\nOK zero-setup verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
