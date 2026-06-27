from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_package_metadata_links_to_real_repository():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "your-org" not in pyproject
    assert 'Homepage = "https://github.com/ly-wang19/engram"' in pyproject
    assert 'Repository = "https://github.com/ly-wang19/engram"' in pyproject
