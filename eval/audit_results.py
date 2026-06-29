"""Audit local benchmark result logs before they become public evidence.

``validate_results.py`` answers "is this one log citable?". This script gives the wider dashboard:
which files are complete, which are partial exploratory runs, and which have schema/errors that must not
be cited. It is intentionally zero-dep and safe to run on a messy local ``results/`` directory.

    python eval/audit_results.py
    python eval/audit_results.py --fail-invalid
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from eval.validate_results import validate_bench_log
except ModuleNotFoundError:  # direct execution as ``python eval/audit_results.py``
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from eval.validate_results import validate_bench_log


DEFAULT_BENCH_ROWS = 500


@dataclass(frozen=True)
class SystemAudit:
    name: str
    scored: int
    errors: int
    missing: int


@dataclass(frozen=True)
class LogAudit:
    path: Path
    rows: int
    schema: str
    systems: tuple[SystemAudit, ...]
    validation_errors: tuple[str, ...]
    status: str


def _load_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], [f"{path}: cannot read file: {exc}"]
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path}:{lineno}: invalid JSON: {exc}")
            continue
        if not isinstance(row, dict):
            errors.append(f"{path}:{lineno}: row must be a JSON object")
            continue
        rows.append(row)
    return rows, errors


def infer_schema(rows: list[dict[str, Any]]) -> str:
    return "personamem" if any("pref_type" in row for row in rows) else "bench"


def expected_rows_for(schema: str, rows: int, bench_rows: int, personamem_rows: int | None) -> int:
    if schema == "bench":
        return bench_rows
    return personamem_rows if personamem_rows is not None else rows


def audit_log(path: Path, *, bench_rows: int = DEFAULT_BENCH_ROWS, personamem_rows: int | None = None) -> LogAudit:
    rows, load_errors = _load_rows(path)
    schema = infer_schema(rows)
    expected_rows = expected_rows_for(schema, len(rows), bench_rows, personamem_rows)
    validation_errors = list(load_errors)
    if not load_errors:
        validation_errors.extend(
            validate_bench_log(path, expected_rows=expected_rows, require_complete=True, schema=schema)
        )

    system_names = sorted({
        name
        for row in rows
        if isinstance(row.get("sys"), dict)
        for name in row["sys"]
        if isinstance(name, str) and name
    })
    audits: list[SystemAudit] = []
    for name in system_names:
        scored = errors = missing = 0
        for row in rows:
            sys_obj = row.get("sys")
            result = sys_obj.get(name) if isinstance(sys_obj, dict) else None
            if not isinstance(result, dict):
                missing += 1
                continue
            if schema == "bench" and result.get("err"):
                errors += 1
            elif result.get("ok") is not None:
                scored += 1
        audits.append(SystemAudit(name=name, scored=scored, errors=errors, missing=missing))

    if validation_errors:
        status = "incomplete" if rows and len(rows) != expected_rows else "invalid"
    else:
        status = "complete"
    return LogAudit(
        path=path,
        rows=len(rows),
        schema=schema,
        systems=tuple(audits),
        validation_errors=tuple(validation_errors),
        status=status,
    )


def format_audit(audits: list[LogAudit]) -> str:
    if not audits:
        return "No JSONL logs found."
    path_w = max(len(str(a.path)) for a in audits)
    schema_w = max(len(a.schema) for a in audits)
    status_w = max(len(a.status) for a in audits)
    lines = [
        "path".ljust(path_w)
        + "  rows  "
        + "schema".ljust(schema_w)
        + "  "
        + "status".ljust(status_w)
        + "  systems",
        "-" * (path_w + schema_w + status_w + 18),
    ]
    for audit in audits:
        systems = ", ".join(
            f"{s.name}:{s.scored}/{audit.rows}"
            + (f" err={s.errors}" if s.errors else "")
            + (f" miss={s.missing}" if s.missing else "")
            for s in audit.systems
        ) or "-"
        lines.append(
            str(audit.path).ljust(path_w)
            + f"  {audit.rows:4d}  "
            + audit.schema.ljust(schema_w)
            + "  "
            + audit.status.ljust(status_w)
            + "  "
            + systems
        )
        if audit.validation_errors:
            for error in audit.validation_errors[:3]:
                lines.append("  " + error)
            if len(audit.validation_errors) > 3:
                lines.append(f"  ... {len(audit.validation_errors) - 3} more validation error(s)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Engram result JSONL logs.")
    parser.add_argument("paths", nargs="*", default=["results/*.jsonl"], help="JSONL files or glob patterns")
    parser.add_argument("--bench-rows", type=int, default=DEFAULT_BENCH_ROWS)
    parser.add_argument("--personamem-rows", type=int, default=None)
    parser.add_argument("--fail-invalid", action="store_true", help="exit non-zero when any log is not complete")
    args = parser.parse_args(argv)

    paths: list[Path] = []
    for pattern in args.paths:
        matches = sorted(Path(match) for match in glob.glob(pattern))
        if matches:
            paths.extend(matches)
        else:
            paths.append(Path(pattern))
    audits = [
        audit_log(path, bench_rows=args.bench_rows, personamem_rows=args.personamem_rows)
        for path in sorted(set(paths))
        if path.suffix == ".jsonl"
    ]
    print(format_audit(audits))
    if args.fail_invalid and any(a.status != "complete" for a in audits):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
