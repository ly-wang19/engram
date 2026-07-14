from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import engram

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"


def test_release_versions_are_consistent():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdk = json.loads((ROOT / "clients/typescript/package.json").read_text(encoding="utf-8"))
    sdk_lock = json.loads(
        (ROOT / "clients/typescript/package-lock.json").read_text(encoding="utf-8")
    )
    frontend = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))

    assert engram.__version__ == VERSION
    assert pyproject["project"]["version"] == VERSION
    assert sdk["version"] == VERSION
    assert sdk_lock["version"] == VERSION
    assert sdk_lock["packages"][""]["version"] == VERSION
    assert frontend["version"] == VERSION
    assert "Development Status :: 4 - Beta" in pyproject["project"]["classifiers"]


def test_commercial_release_assets_exist():
    required = (
        "Dockerfile",
        ".dockerignore",
        "SECURITY.md",
        "CHANGELOG.md",
        "deploy/docker-compose.yml",
        "deploy/.env.example",
        "deploy/README.md",
        "docs/commercial-release-0.1.0.zh-CN.md",
        ".github/workflows/ci.yml",
        "scripts/check_release.py",
    )

    missing = [path for path in required if not (ROOT / path).is_file()]
    assert missing == []


def test_release_gate_passes():
    result = subprocess.run(
        [sys.executable, "scripts/check_release.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK commercial release checks passed" in result.stdout
