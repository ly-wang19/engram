from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_package_metadata_links_to_real_repository():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ts_package_text = (ROOT / "clients/typescript/package.json").read_text(encoding="utf-8")
    ts_package = json.loads(ts_package_text)
    ts_readme = (ROOT / "clients/typescript/README.md").read_text(encoding="utf-8")
    ts_index = (ROOT / "clients/typescript/src/index.ts").read_text(encoding="utf-8")

    assert "your-org" not in pyproject
    assert "your-org" not in ts_package_text
    assert "your-org" not in ts_readme
    assert "your-org" not in ts_index
    assert 'Homepage = "https://github.com/ly-wang19/engram"' in pyproject
    assert 'Repository = "https://github.com/ly-wang19/engram"' in pyproject
    assert 'Documentation = "https://github.com/ly-wang19/engram#readme"' in pyproject
    assert 'Issues = "https://github.com/ly-wang19/engram/issues"' in pyproject
    assert ts_package["repository"]["url"] == "https://github.com/ly-wang19/engram"
    assert ts_package["repository"]["directory"] == "clients/typescript"
    assert ts_package["homepage"] == "https://github.com/ly-wang19/engram/tree/main/clients/typescript"
    assert ts_package["bugs"]["url"] == "https://github.com/ly-wang19/engram/issues"
    assert ts_package["scripts"]["prepublishOnly"] == "npm run typecheck && npm run build && npm test"
    assert "](../../" not in ts_readme
    assert "https://github.com/ly-wang19/engram/blob/main/LICENSE" in ts_readme
    assert "https://github.com/ly-wang19/engram/blob/main/COMMERCIAL-LICENSE.md" in ts_readme
