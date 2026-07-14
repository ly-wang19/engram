from __future__ import annotations

import importlib
import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_package_metadata_links_to_real_repository():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ts_package_text = (ROOT / "clients/typescript/package.json").read_text(encoding="utf-8")
    ts_package = json.loads(ts_package_text)
    ts_readme = (ROOT / "clients/typescript/README.md").read_text(encoding="utf-8")
    ts_index = (ROOT / "clients/typescript/src/index.ts").read_text(encoding="utf-8")
    quickstart_example = (ROOT / "examples/quickstart.py").read_text(encoding="utf-8")
    zero_setup = (ROOT / "scripts/check_zero_setup.py").read_text(encoding="utf-8")

    assert "your-org" not in pyproject
    assert "your-org" not in ts_package_text
    assert "your-org" not in ts_readme
    assert "your-org" not in ts_index
    assert 'Homepage = "https://github.com/ly-wang19/engram"' in pyproject
    assert 'Repository = "https://github.com/ly-wang19/engram"' in pyproject
    assert 'Documentation = "https://github.com/ly-wang19/engram#readme"' in pyproject
    assert 'Issues = "https://github.com/ly-wang19/engram/issues"' in pyproject
    assert 'license-files = ["LICENSE", "COMMERCIAL-LICENSE.md"]' in pyproject
    assert 'engram-quickstart = "engram.quickstart:main"' in pyproject
    assert "from engram.quickstart import main" in quickstart_example
    assert '"-m", "engram.quickstart"' in zero_setup
    for module in (
        "engram.agent_doctor",
        "engram.agent_setup",
        "engram.connectors",
        "engram.mcp",
        "engram.store.migrate",
    ):
        assert f'"{module}"' in zero_setup
    assert '"engram-agent-bootstrap"' in zero_setup
    assert "bootstrap_main(['--help'])" in zero_setup
    assert ts_package["repository"]["url"] == "https://github.com/ly-wang19/engram"
    assert ts_package["repository"]["directory"] == "clients/typescript"
    assert ts_package["homepage"] == "https://github.com/ly-wang19/engram/tree/main/clients/typescript"
    assert ts_package["bugs"]["url"] == "https://github.com/ly-wang19/engram/issues"
    assert ts_package["scripts"]["prepublishOnly"] == "npm run typecheck && npm run build && npm test"
    assert "](../../" not in ts_readme
    assert "https://github.com/ly-wang19/engram/blob/main/LICENSE" in ts_readme
    assert "https://github.com/ly-wang19/engram/blob/main/COMMERCIAL-LICENSE.md" in ts_readme


def test_python_console_scripts_resolve_to_callables():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert scripts
    for name, target in scripts.items():
        module_name, _, attr = target.partition(":")
        assert module_name and attr, f"{name} target must be module:callable"
        module = importlib.import_module(module_name)
        func = getattr(module, attr, None)
        assert callable(func), f"{name} target {target} is not callable"


def test_zero_setup_contract_mentions_installed_quickstart():
    for rel in ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "engram-quickstart" in text, f"{rel} must include the installed quickstart contract"


def test_console_seed_default_names_jsonl_stores_not_pickles():
    seed_console = (ROOT / "eval/seed_console.py").read_text(encoding="utf-8")

    assert 'default="/tmp/console_stores"' in seed_console
    assert "JSONL store directories" in seed_console
    assert "console_pkls" not in seed_console
    assert "sys.path.insert(0, str(ROOT))" in seed_console
