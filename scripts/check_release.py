#!/usr/bin/env python3
"""Zero-dependency release gate for Engram's self-hosted commercial distribution."""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _check(condition: bool, message: str, errors: list[str]) -> None:
    marker = "PASS" if condition else "FAIL"
    print(f"[{marker}] {message}")
    if not condition:
        errors.append(message)


def main() -> int:
    errors: list[str] = []
    pyproject = tomllib.loads(_read("pyproject.toml"))
    sdk = json.loads(_read("clients/typescript/package.json"))
    sdk_lock = json.loads(_read("clients/typescript/package-lock.json"))
    frontend = json.loads(_read("frontend/package.json"))
    init_text = _read("engram/__init__.py")
    init_version = re.search(r'^__version__\s*=\s*"([^"]+)"', init_text, re.MULTILINE)

    versions = {
        "python package": pyproject["project"]["version"],
        "python runtime": init_version.group(1) if init_version else "missing",
        "TypeScript SDK": sdk["version"],
        "TypeScript lock": sdk_lock["version"],
        "TypeScript lock root": sdk_lock["packages"][""]["version"],
        "management console": frontend["version"],
    }
    for component, version in versions.items():
        _check(version == VERSION, f"{component} version is {VERSION}", errors)

    _check(
        "Development Status :: 4 - Beta" in pyproject["project"]["classifiers"],
        "package status is Beta",
        errors,
    )
    _check(
        pyproject["project"]["license"] == "AGPL-3.0-only",
        "package license is AGPL-3.0-only",
        errors,
    )

    required = (
        "LICENSE",
        "COMMERCIAL-LICENSE.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "Dockerfile",
        ".dockerignore",
        "deploy/docker-compose.yml",
        "deploy/.env.example",
        "deploy/README.md",
        "README.md",
        "README.zh-CN.md",
        "API.md",
        "docs/commercial-release-0.1.0.zh-CN.md",
        "docs/architecture-optimization-map.zh-CN.md",
        "docs/engram-full-architecture-report.zh-CN.md",
        ".github/workflows/ci.yml",
    )
    for path in required:
        _check((ROOT / path).is_file(), f"required release file exists: {path}", errors)

    docker_text = _read("Dockerfile")
    compose_text = _read("deploy/docker-compose.yml")
    _check("USER 10001:10001" in docker_text, "container runs as non-root", errors)
    _check("ENGRAM_OPEN" not in docker_text + compose_text, "container is not open by default", errors)
    _check("/ready" in docker_text + compose_text, "container health uses readiness", errors)

    results = _read("RESULTS.md")
    evidence = sorted(set(re.findall(r"\]\((results/[^)]+\.jsonl)\)", results)))
    _check(bool(evidence), "RESULTS.md links raw JSONL evidence", errors)
    for path in evidence:
        _check((ROOT / path).is_file(), f"public evidence exists: {path}", errors)

    _check("商业授权" in _read("COMMERCIAL-LICENSE.md"), "commercial license has Chinese terms", errors)
    _check("漏洞" in _read("SECURITY.md"), "security policy has Chinese reporting guidance", errors)
    _check("单节点" in _read("docs/commercial-release-0.1.0.zh-CN.md"), "release scope is bounded", errors)

    if errors:
        print(f"\ncommercial release checks failed: {len(errors)}", file=sys.stderr)
        return 1
    print("\nOK commercial release checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
