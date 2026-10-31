"""Credential routing is stdlib-only and never contacts a server or reads real credentials."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from engram.mcp import backends as B


@pytest.fixture(autouse=True)
def clean_credentials(monkeypatch):
    for name in ("ENGRAM_API_URL", "ENGRAM_API_KEY", "ENGRAM_API_KEY_FILE", "ENGRAM_NAMESPACE"):
        monkeypatch.setenv(name, "")


def test_explicit_file_beats_ambient_key_but_explicit_key_wins(tmp_path, monkeypatch):
    key_file = tmp_path / "watch.key"
    key_file.write_text("  shared-file-key\n")
    monkeypatch.setenv("ENGRAM_API_KEY", "ambient-key")
    assert B.resolve_api_key(api_key_file=str(key_file)) == "shared-file-key"
    assert B.resolve_api_key("explicit-key", str(tmp_path / "missing")) == "explicit-key"
    assert B.resolve_api_key() == "ambient-key"
    monkeypatch.setenv("ENGRAM_API_KEY_FILE", str(key_file))
    assert B.resolve_api_key() == "ambient-key"
    monkeypatch.delenv("ENGRAM_API_KEY")
    assert B.resolve_api_key() == "shared-file-key"


@pytest.mark.parametrize("contents", [None, b" \n", b"\xff"])
def test_selected_invalid_file_fails_without_fallback(tmp_path, monkeypatch, contents):
    key_file = tmp_path / "watch.key"
    if contents is not None:
        key_file.write_bytes(contents)
    monkeypatch.setenv("ENGRAM_API_KEY", "must-not-be-used")
    with pytest.raises(ValueError) as exc:
        B.resolve_api_key(api_key_file=str(key_file))
    assert "must-not-be-used" not in str(exc.value)


def test_backend_environment_key_file_selects_remote_credential(tmp_path, monkeypatch):
    key_file = tmp_path / "watch.key"
    key_file.write_text("shared-file-key")
    monkeypatch.setenv("ENGRAM_API_URL", "http://engram.test")
    monkeypatch.setenv("ENGRAM_API_KEY_FILE", str(key_file))
    monkeypatch.setattr(B, "RemoteBackend", lambda url, key: (url, key))
    assert B.make_backend() == ("http://engram.test", "shared-file-key")


def test_mcp_cli_file_reaches_backend_without_printing_key(tmp_path, monkeypatch, capsys):
    from engram.mcp.__main__ import main

    key_file = tmp_path / "watch.key"
    key_file.write_text("shared-file-key")
    monkeypatch.setenv("ENGRAM_API_KEY", "ambient-key")
    installed = []
    fake_server = SimpleNamespace(
        set_backend=installed.append,
        mcp=SimpleNamespace(run=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "engram.mcp.server", fake_server)
    monkeypatch.setattr(B, "RemoteBackend", lambda url, key: SimpleNamespace(
        key=key, describe=lambda: "remote test backend"))
    monkeypatch.setattr(sys, "argv", ["engram.mcp", "--api-url", "http://engram.test",
                                      "--api-key-file", str(key_file)])
    main()
    assert installed[0].key == "shared-file-key"
    output = capsys.readouterr()
    assert "shared-file-key" not in output.out + output.err
    assert "ambient-key" not in output.out + output.err
