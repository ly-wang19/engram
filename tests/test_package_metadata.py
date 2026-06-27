from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_package_metadata_links_to_real_repository():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ts_package = (ROOT / "clients/typescript/package.json").read_text(encoding="utf-8")
    ts_readme = (ROOT / "clients/typescript/README.md").read_text(encoding="utf-8")
    ts_index = (ROOT / "clients/typescript/src/index.ts").read_text(encoding="utf-8")

    assert "your-org" not in pyproject
    assert "your-org" not in ts_package
    assert "your-org" not in ts_readme
    assert "your-org" not in ts_index
    assert 'Homepage = "https://github.com/ly-wang19/engram"' in pyproject
    assert 'Repository = "https://github.com/ly-wang19/engram"' in pyproject
    assert 'Documentation = "https://github.com/ly-wang19/engram#readme"' in pyproject
    assert 'Issues = "https://github.com/ly-wang19/engram/issues"' in pyproject
    assert '"url": "https://github.com/ly-wang19/engram"' in ts_package
    assert '"homepage": "https://github.com/ly-wang19/engram/tree/main/clients/typescript"' in ts_package
    assert '"bugs": { "url": "https://github.com/ly-wang19/engram/issues" }' in ts_package
