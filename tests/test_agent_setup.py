from __future__ import annotations

import json

import pytest

from engram.agent_setup import (
    POLICY_END_MARKER,
    POLICY_START_MARKER,
    agents_policy_block,
    bootstrap_main,
    codex_toml_block,
    install_agents_policy,
    install_codex_config,
    install_mcp_json_config,
    main,
    mcp_args,
    parse_bootstrap_targets,
    render_agents_policy_result,
    render_codex_config_result,
    render_markdown,
    uninstall_agents_policy,
    uninstall_codex_config,
    uninstall_mcp_json_config,
)


def test_mcp_args_remote_and_local():
    assert mcp_args(api_url="http://localhost:8000", api_key="me", namespace=None) == [
        "-m", "engram.mcp", "--api-url", "http://localhost:8000", "--api-key", "me",
    ]
    assert mcp_args(api_url=None, api_key=None, namespace="work") == [
        "-m", "engram.mcp", "--namespace", "work",
    ]


def test_parse_bootstrap_targets_is_stable_and_validates():
    assert parse_bootstrap_targets("mcp-json,codex,mcp-json") == ["codex", "mcp-json"]
    with pytest.raises(ValueError):
        parse_bootstrap_targets("codex,unknown")


def test_render_codex_setup_contains_lifecycle_and_config():
    out = render_markdown(
        "codex",
        api_url="http://localhost:8000",
        api_key="me",
        namespace=None,
        session_id="codex:repo:thread",
        python_cmd="/opt/engram/bin/python",
    )

    assert "codex mcp add engram -- /opt/engram/bin/python" in out
    assert "[mcp_servers.engram]" in out
    assert 'command = "/opt/engram/bin/python"' in out
    assert "--api-url" in out and "--api-key" in out
    assert "engram_recall" in out
    assert "engram_remember" in out
    assert "engram_close_session" in out
    assert "engram_update_fact" in out
    assert "engram_delete_fact" in out
    assert "engram_export" in out
    assert "engram_set_focus" in out
    assert "codex:<project>:<thread>" in out


def test_render_local_claude_setup_uses_namespace_not_api_key():
    out = render_markdown(
        "claude-code",
        api_url=None,
        api_key=None,
        namespace="local-me",
        session_id="claude-code:repo:thread",
    )

    assert "claude mcp add-json engram" in out
    assert "--namespace" in out
    assert "local-me" in out
    assert "--api-key" not in out
    assert ".mcp.json" in out


def test_install_mcp_json_config_creates_file(tmp_path):
    config = tmp_path / ".mcp.json"

    result = install_mcp_json_config(
        config_path=str(config),
        api_url="http://engram.test",
        api_key="sk-test",
        namespace=None,
        python_cmd="/opt/engram/bin/python",
    )

    assert result["changed"] is True
    assert result["backup"] is None
    data = json.loads(config.read_text())
    assert data["mcpServers"]["engram"] == {
        "type": "stdio",
        "command": "/opt/engram/bin/python",
        "args": ["-m", "engram.mcp", "--api-url", "http://engram.test", "--api-key", "sk-test"],
    }


def test_install_mcp_json_config_preserves_other_servers_and_backs_up(tmp_path):
    config = tmp_path / ".mcp.json"
    config.write_text(json.dumps({
        "mcpServers": {
            "other": {"command": "other", "args": []},
            "engram": {"command": "old", "args": ["old"]},
        },
        "notes": {"keep": True},
    }))

    result = install_mcp_json_config(
        config_path=str(config),
        api_url=None,
        api_key=None,
        namespace="local-me",
        python_cmd="/opt/engram/bin/python",
    )

    assert result["changed"] is True
    assert result["backup"] is not None
    data = json.loads(config.read_text())
    assert data["notes"] == {"keep": True}
    assert data["mcpServers"]["other"] == {"command": "other", "args": []}
    assert data["mcpServers"]["engram"]["args"] == ["-m", "engram.mcp", "--namespace", "local-me"]


def test_install_mcp_json_config_dry_run_does_not_write(tmp_path):
    config = tmp_path / ".mcp.json"
    config.write_text('{"mcpServers": {}}\n')

    result = install_mcp_json_config(
        config_path=str(config),
        api_url="http://engram.test",
        api_key="me",
        namespace=None,
        python_cmd="python",
        dry_run=True,
    )

    assert result["changed"] is True
    assert result["dry_run"] is True
    assert result["backup"] is None
    assert config.read_text() == '{"mcpServers": {}}\n'


def test_install_agents_policy_creates_managed_block(tmp_path):
    agents = tmp_path / "AGENTS.md"

    result = install_agents_policy(agents_file=str(agents), session_id="codex:repo:thread")

    assert result["changed"] is True
    assert result["backup"] is None
    text = agents.read_text()
    assert POLICY_START_MARKER in text
    assert POLICY_END_MARKER in text
    assert "codex:repo:thread" in text
    assert text == agents_policy_block("codex:repo:thread")


def test_install_agents_policy_replaces_only_managed_block_and_backs_up(tmp_path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        "# Existing Rules\n\n"
        "Keep this project rule.\n\n"
        f"{POLICY_START_MARKER}\nold Engram instructions\n{POLICY_END_MARKER}\n\n"
        "Keep this footer.\n"
    )

    result = install_agents_policy(
        agents_file=str(agents),
        session_id="claude-code:repo:new-thread",
    )

    assert result["changed"] is True
    assert result["backup"] is not None
    backup = (tmp_path / result["backup"]).read_text()
    assert "old Engram instructions" in backup
    text = agents.read_text()
    assert "# Existing Rules" in text
    assert "Keep this project rule." in text
    assert "Keep this footer." in text
    assert "old Engram instructions" not in text
    assert "claude-code:repo:new-thread" in text
    assert text.count(POLICY_START_MARKER) == 1


def test_install_agents_policy_dry_run_does_not_write(tmp_path):
    agents = tmp_path / "AGENTS.md"

    result = install_agents_policy(
        agents_file=str(agents),
        session_id="codex:repo:thread",
        dry_run=True,
    )

    assert result["changed"] is True
    assert result["backup"] is None
    assert not agents.exists()
    report = render_agents_policy_result(result)
    assert "Would install" in report
    assert POLICY_START_MARKER in report


def test_uninstall_agents_policy_removes_only_managed_block_and_backs_up(tmp_path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        "# Existing Rules\n\n"
        "Keep this project rule.\n\n"
        f"{agents_policy_block('codex:repo:thread')}\n"
        "Keep this footer.\n"
    )

    result = uninstall_agents_policy(agents_file=str(agents))

    assert result["changed"] is True
    assert result["backup"] is not None
    backup = (tmp_path / result["backup"]).read_text()
    assert POLICY_START_MARKER in backup
    text = agents.read_text()
    assert POLICY_START_MARKER not in text
    assert POLICY_END_MARKER not in text
    assert "# Existing Rules" in text
    assert "Keep this project rule." in text
    assert "Keep this footer." in text


def test_uninstall_mcp_json_config_removes_only_engram_server(tmp_path):
    config = tmp_path / ".mcp.json"
    config.write_text(json.dumps({
        "mcpServers": {
            "engram": {"command": "python", "args": ["-m", "engram.mcp"]},
            "other": {"command": "other", "args": []},
        }
    }))

    result = uninstall_mcp_json_config(config_path=str(config))

    assert result["changed"] is True
    assert result["backup"] is not None
    data = json.loads(config.read_text())
    assert "engram" not in data["mcpServers"]
    assert data["mcpServers"]["other"] == {"command": "other", "args": []}


def test_render_openai_setup_contains_memory_extension_and_close_call():
    out = render_markdown(
        "openai",
        api_url="http://engram.test",
        api_key="sk-test",
        namespace=None,
        session_id="app:product:conversation",
    )

    assert "OpenAI(base_url='http://engram.test/v1'" in out
    assert "'session_id': session_id" in out
    assert "'scope': 'auto'" in out
    assert "/v1/sessions/close" in out
    assert "Authorization: Bearer sk-test" in out


def test_install_codex_config_creates_file(tmp_path):
    config = tmp_path / "codex" / "config.toml"

    result = install_codex_config(
        config_path=str(config),
        api_url="http://engram.test",
        api_key="sk-test",
        namespace=None,
        python_cmd="/opt/engram/bin/python",
    )

    assert result["changed"] is True
    assert result["backup"] is None
    text = config.read_text()
    assert "[mcp_servers.engram]" in text
    assert 'command = "/opt/engram/bin/python"' in text
    assert '"--api-url", "http://engram.test", "--api-key", "sk-test"' in text


def test_install_codex_config_replaces_engram_table_and_backs_up(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        'model = "gpt-5"\n\n'
        "[mcp_servers.other]\n"
        'command = "other"\n\n'
        "[mcp_servers.engram]\n"
        'command = "old-python"\n'
        'args = ["old"]\n\n'
        "[profiles.default]\n"
        'approval_policy = "never"\n'
    )

    result = install_codex_config(
        config_path=str(config),
        api_url=None,
        api_key=None,
        namespace="local-me",
        python_cmd="/opt/engram/bin/python",
    )

    assert result["changed"] is True
    assert result["backup"] is not None
    assert "old-python" in (tmp_path / result["backup"]).read_text()
    text = config.read_text()
    assert 'model = "gpt-5"' in text
    assert "[mcp_servers.other]" in text
    assert "[profiles.default]" in text
    assert "old-python" not in text
    assert '"--namespace", "local-me"' in text


def test_install_codex_config_dry_run_does_not_write(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5"\n')

    result = install_codex_config(
        config_path=str(config),
        api_url="http://engram.test",
        api_key="me",
        namespace=None,
        python_cmd="python",
        dry_run=True,
    )

    assert result["changed"] is True
    assert result["dry_run"] is True
    assert result["backup"] is None
    assert config.read_text() == 'model = "gpt-5"\n'
    report = render_codex_config_result(result, python_cmd="python")
    assert "Would install" in report
    assert "[mcp_servers.engram]" in report


def test_render_codex_config_result_verify_command_matches_remote_mode(tmp_path):
    config = tmp_path / "config.toml"
    result = install_codex_config(
        config_path=str(config),
        api_url="http://engram.test",
        api_key="sk-test",
        namespace=None,
        python_cmd="/opt/engram/bin/python",
        dry_run=True,
    )

    report = render_codex_config_result(
        result,
        python_cmd="/opt/engram/bin/python",
        api_url="http://engram.test",
        api_key="sk-test",
    )

    assert (
        "engram-agent-doctor --client codex --python /opt/engram/bin/python "
        "--api-url http://engram.test --api-key sk-test"
    ) in report


def test_render_codex_config_result_verify_command_matches_local_namespace(tmp_path):
    config = tmp_path / "config.toml"
    result = install_codex_config(
        config_path=str(config),
        api_url=None,
        api_key=None,
        namespace="local-me",
        python_cmd="/opt/engram/bin/python",
        dry_run=True,
    )

    report = render_codex_config_result(
        result,
        python_cmd="/opt/engram/bin/python",
        api_url=None,
        api_key="local-me",
    )

    assert (
        "engram-agent-doctor --client codex --python /opt/engram/bin/python "
        "--api-key local-me"
    ) in report
    assert '"--namespace", "local-me"' in report


def test_render_codex_config_result_includes_doctor_report(tmp_path):
    config = tmp_path / "config.toml"
    result = install_codex_config(
        config_path=str(config),
        api_url="http://engram.test",
        api_key="me",
        namespace=None,
        python_cmd="python",
    )
    report = {
        "ok": True,
        "client": "codex",
        "python": "python",
        "summary": {"ok": 4, "warn": 0, "fail": 0},
        "checks": [{"name": "MCP stdio server", "status": "ok", "detail": "ready"}],
    }

    text = render_codex_config_result(result, python_cmd="python", doctor_report=report)

    assert "Doctor result:" in text
    assert "PASS MCP stdio server" in text
    assert "Cross-agent memory runtime is ready" in text


def test_main_install_codex_with_doctor_runs_doctor(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    calls = []

    def fake_doctor(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "client": "codex",
            "python": kwargs["python_cmd"],
            "api_url": kwargs["api_url"],
            "summary": {"ok": 4, "warn": 0, "fail": 0},
            "checks": [{"name": "MCP stdio server", "status": "ok", "detail": "ready"}],
        }

    monkeypatch.setattr("engram.agent_setup.run_codex_doctor", fake_doctor)

    main([
        "--install-codex",
        "--doctor",
        "--codex-config",
        str(config),
        "--python",
        "/opt/engram/bin/python",
        "--api-url",
        "http://engram.test",
        "--api-key",
        "me",
    ])

    assert calls[0]["python_cmd"] == "/opt/engram/bin/python"
    assert calls[0]["api_url"] == "http://engram.test"
    assert calls[0]["api_key"] == "me"
    assert calls[0]["codex_config"] == str(config)
    out = capsys.readouterr().out
    assert "Installed:" in out
    assert "Doctor result:" in out
    assert "PASS MCP stdio server" in out


def test_main_install_codex_dry_run_skips_doctor(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"

    def fake_doctor(**kwargs):
        raise AssertionError(kwargs)

    monkeypatch.setattr("engram.agent_setup.run_codex_doctor", fake_doctor)

    main([
        "--install-codex",
        "--doctor",
        "--dry-run",
        "--codex-config",
        str(config),
    ])

    assert not config.exists()
    out = capsys.readouterr().out
    assert "Would install" in out
    assert "Doctor skipped" in out


def test_main_install_codex_with_failed_doctor_exits_nonzero(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"

    def fake_doctor(**kwargs):
        return {
            "ok": False,
            "client": "codex",
            "python": kwargs["python_cmd"],
            "summary": {"ok": 1, "warn": 0, "fail": 1},
            "checks": [{"name": "MCP stdio server", "status": "fail", "detail": "boom"}],
        }

    monkeypatch.setattr("engram.agent_setup.run_codex_doctor", fake_doctor)

    with pytest.raises(SystemExit) as exc:
        main([
            "--install-codex",
            "--doctor",
            "--codex-config",
            str(config),
        ])

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "FAIL MCP stdio server" in out


def test_main_install_mcp_json_with_doctor_runs_doctor(tmp_path, monkeypatch, capsys):
    config = tmp_path / ".mcp.json"
    calls = []

    def fake_doctor(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "client": kwargs["client"],
            "python": kwargs["python_cmd"],
            "api_url": kwargs["api_url"],
            "summary": {"ok": 3, "warn": 0, "fail": 0},
            "checks": [{"name": "MCP stdio server", "status": "ok", "detail": "ready"}],
        }

    monkeypatch.setattr("engram.agent_setup.run_mcp_doctor", fake_doctor)

    main([
        "--install-mcp-json",
        "--doctor",
        "--doctor-client",
        "claude-code",
        "--mcp-json",
        str(config),
        "--python",
        "/opt/engram/bin/python",
        "--api-url",
        "http://engram.test",
        "--api-key",
        "me",
    ])

    assert calls[0]["client"] == "claude-code"
    assert calls[0]["python_cmd"] == "/opt/engram/bin/python"
    assert calls[0]["api_url"] == "http://engram.test"
    assert calls[0]["api_key"] == "me"
    assert calls[0]["mcp_json"] == str(config)
    out = capsys.readouterr().out
    assert "Installed:" in out
    assert "Doctor result:" in out
    assert "PASS MCP stdio server" in out


def test_main_install_mcp_json_accepts_doctor_client_none(tmp_path, monkeypatch, capsys):
    config = tmp_path / ".mcp.json"
    calls = []

    def fake_doctor(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "client": kwargs["client"],
            "python": kwargs["python_cmd"],
            "summary": {"ok": 2, "warn": 0, "fail": 0},
            "checks": [{"name": "MCP stdio server", "status": "ok", "detail": "ready"}],
        }

    monkeypatch.setattr("engram.agent_setup.run_mcp_doctor", fake_doctor)

    main([
        "--install-mcp-json",
        "--doctor",
        "--doctor-client",
        "none",
        "--mcp-json",
        str(config),
    ])

    assert calls[0]["client"] == "none"
    assert "Doctor result:" in capsys.readouterr().out


def test_main_install_mcp_json_dry_run_skips_doctor(tmp_path, monkeypatch, capsys):
    config = tmp_path / ".mcp.json"

    def fake_doctor(**kwargs):
        raise AssertionError(kwargs)

    monkeypatch.setattr("engram.agent_setup.run_mcp_doctor", fake_doctor)

    main([
        "--install-mcp-json",
        "--doctor",
        "--dry-run",
        "--mcp-json",
        str(config),
    ])

    assert not config.exists()
    out = capsys.readouterr().out
    assert "Would install" in out
    assert "Doctor skipped" in out


def test_main_bootstrap_dry_run_does_not_write(tmp_path, monkeypatch, capsys):
    codex = tmp_path / "codex.toml"
    mcp_json = tmp_path / ".mcp.json"

    def fail_doctor(**kwargs):
        raise AssertionError(kwargs)

    monkeypatch.setattr("engram.agent_setup.run_codex_doctor", fail_doctor)
    monkeypatch.setattr("engram.agent_setup.run_mcp_doctor", fail_doctor)

    main([
        "--bootstrap",
        "--dry-run",
        "--codex-config",
        str(codex),
        "--mcp-json",
        str(mcp_json),
        "--session-id",
        "codex:repo:thread",
    ])

    assert not codex.exists()
    assert not mcp_json.exists()
    out = capsys.readouterr().out
    assert "# Engram Agent Bootstrap" in out
    assert "Codex: would install" in out
    assert "MCP JSON: would install" in out
    assert "Doctor skipped" in out
    assert "Suggested AGENTS.md note" in out


def test_main_bootstrap_install_policy_dry_run_does_not_write(tmp_path, monkeypatch, capsys):
    codex = tmp_path / "codex.toml"
    mcp_json = tmp_path / ".mcp.json"
    agents = tmp_path / "AGENTS.md"

    def fail_doctor(**kwargs):
        raise AssertionError(kwargs)

    monkeypatch.setattr("engram.agent_setup.run_codex_doctor", fail_doctor)
    monkeypatch.setattr("engram.agent_setup.run_mcp_doctor", fail_doctor)

    main([
        "--bootstrap",
        "--install-policy",
        "--dry-run",
        "--codex-config",
        str(codex),
        "--mcp-json",
        str(mcp_json),
        "--agents-file",
        str(agents),
        "--session-id",
        "codex:repo:thread",
    ])

    assert not codex.exists()
    assert not mcp_json.exists()
    assert not agents.exists()
    out = capsys.readouterr().out
    assert "Codex: would install" in out
    assert "MCP JSON: would install" in out
    assert "AGENTS.md policy: would install" in out
    assert "Managed AGENTS.md policy block" in out
    assert "codex:repo:thread" in out


def test_main_bootstrap_installs_both_and_runs_codex_doctor(tmp_path, monkeypatch, capsys):
    codex = tmp_path / "codex.toml"
    mcp_json = tmp_path / ".mcp.json"
    calls = []

    def fake_codex_doctor(**kwargs):
        calls.append(("codex", kwargs))
        return {
            "ok": True,
            "client": "codex",
            "python": kwargs["python_cmd"],
            "summary": {"ok": 4, "warn": 0, "fail": 0},
            "checks": [{"name": "MCP stdio server", "status": "ok", "detail": "ready"}],
        }

    monkeypatch.setattr("engram.agent_setup.run_codex_doctor", fake_codex_doctor)

    main([
        "--bootstrap",
        "--codex-config",
        str(codex),
        "--mcp-json",
        str(mcp_json),
        "--python",
        "/opt/engram/bin/python",
        "--api-url",
        "http://engram.test",
        "--api-key",
        "me",
    ])

    assert calls[0][0] == "codex"
    assert calls[0][1]["python_cmd"] == "/opt/engram/bin/python"
    assert calls[0][1]["api_url"] == "http://engram.test"
    assert calls[0][1]["api_key"] == "me"
    assert calls[0][1]["codex_config"] == str(codex)
    assert calls[0][1]["mcp_json"] == str(mcp_json)
    assert "[mcp_servers.engram]" in codex.read_text()
    assert json.loads(mcp_json.read_text())["mcpServers"]["engram"]["command"] == "/opt/engram/bin/python"
    out = capsys.readouterr().out
    assert "Codex: installed" in out
    assert "MCP JSON: installed" in out
    assert "Doctor result:" in out


def test_main_bootstrap_local_namespace_is_installed_and_verified(tmp_path, monkeypatch, capsys):
    codex = tmp_path / "codex.toml"
    mcp_json = tmp_path / ".mcp.json"
    calls = []

    def fake_codex_doctor(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "client": "codex",
            "python": kwargs["python_cmd"],
            "summary": {"ok": 4, "warn": 0, "fail": 0},
            "checks": [{"name": "MCP stdio server", "status": "ok", "detail": "ready"}],
        }

    monkeypatch.setattr("engram.agent_setup.run_codex_doctor", fake_codex_doctor)

    main([
        "--bootstrap",
        "--local",
        "--namespace",
        "local-me",
        "--codex-config",
        str(codex),
        "--mcp-json",
        str(mcp_json),
        "--python",
        "/opt/engram/bin/python",
    ])

    assert calls[0]["api_url"] is None
    assert calls[0]["api_key"] == "local-me"
    assert '"--namespace", "local-me"' in codex.read_text()
    server = json.loads(mcp_json.read_text())["mcpServers"]["engram"]
    assert server["args"] == ["-m", "engram.mcp", "--namespace", "local-me"]
    out = capsys.readouterr().out
    assert "--api-key local-me" in out
    assert "Codex: installed" in out
    assert "MCP JSON: installed" in out


def test_main_bootstrap_install_policy_no_doctor_writes_all_targets(tmp_path, capsys):
    codex = tmp_path / "codex.toml"
    mcp_json = tmp_path / ".mcp.json"
    agents = tmp_path / "AGENTS.md"

    main([
        "--bootstrap",
        "--install-policy",
        "--no-doctor",
        "--codex-config",
        str(codex),
        "--mcp-json",
        str(mcp_json),
        "--agents-file",
        str(agents),
        "--python",
        "/opt/engram/bin/python",
        "--session-id",
        "codex:repo:thread",
    ])

    assert "[mcp_servers.engram]" in codex.read_text()
    assert json.loads(mcp_json.read_text())["mcpServers"]["engram"]["command"] == "/opt/engram/bin/python"
    text = agents.read_text()
    assert POLICY_START_MARKER in text
    assert "codex:repo:thread" in text
    out = capsys.readouterr().out
    assert "Codex: installed" in out
    assert "MCP JSON: installed" in out
    assert "AGENTS.md policy: installed" in out
    assert "Doctor skipped: --no-doctor was set" in out


def test_main_bootstrap_mcp_json_only_runs_mcp_doctor(tmp_path, monkeypatch, capsys):
    codex = tmp_path / "codex.toml"
    mcp_json = tmp_path / ".mcp.json"
    calls = []

    def fake_mcp_doctor(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "client": kwargs["client"],
            "python": kwargs["python_cmd"],
            "summary": {"ok": 2, "warn": 0, "fail": 0},
            "checks": [{"name": "MCP stdio server", "status": "ok", "detail": "ready"}],
        }

    monkeypatch.setattr("engram.agent_setup.run_mcp_doctor", fake_mcp_doctor)

    main([
        "--bootstrap",
        "--bootstrap-targets",
        "mcp-json",
        "--doctor-client",
        "none",
        "--codex-config",
        str(codex),
        "--mcp-json",
        str(mcp_json),
    ])

    assert calls[0]["client"] == "none"
    assert calls[0]["mcp_json"] == str(mcp_json)
    assert not codex.exists()
    assert mcp_json.exists()
    assert "MCP JSON: installed" in capsys.readouterr().out


def test_main_bootstrap_failed_doctor_exits_nonzero(tmp_path, monkeypatch, capsys):
    codex = tmp_path / "codex.toml"

    def fake_codex_doctor(**kwargs):
        return {
            "ok": False,
            "client": "codex",
            "python": kwargs["python_cmd"],
            "summary": {"ok": 1, "warn": 0, "fail": 1},
            "checks": [{"name": "MCP stdio server", "status": "fail", "detail": "boom"}],
        }

    monkeypatch.setattr("engram.agent_setup.run_codex_doctor", fake_codex_doctor)

    with pytest.raises(SystemExit) as exc:
        main(["--bootstrap", "--bootstrap-targets", "codex", "--codex-config", str(codex)])

    assert exc.value.code == 1
    assert "FAIL MCP stdio server" in capsys.readouterr().out


def test_main_install_and_uninstall_policy(tmp_path, capsys):
    agents = tmp_path / "AGENTS.md"

    main([
        "--install-policy",
        "--agents-file",
        str(agents),
        "--session-id",
        "codex:repo:thread",
    ])

    assert POLICY_START_MARKER in agents.read_text()
    out = capsys.readouterr().out
    assert "Installed:" in out

    main(["--uninstall-policy", "--agents-file", str(agents)])

    assert POLICY_START_MARKER not in agents.read_text()
    out = capsys.readouterr().out
    assert "Uninstalled:" in out


def test_bootstrap_main_adds_bootstrap_flag(tmp_path, monkeypatch, capsys):
    codex = tmp_path / "codex.toml"

    def fake_codex_doctor(**kwargs):
        return {
            "ok": True,
            "client": "codex",
            "python": kwargs["python_cmd"],
            "summary": {"ok": 1, "warn": 0, "fail": 0},
            "checks": [{"name": "MCP stdio server", "status": "ok", "detail": "ready"}],
        }

    monkeypatch.setattr("engram.agent_setup.run_codex_doctor", fake_codex_doctor)

    bootstrap_main(["--bootstrap-targets", "codex", "--codex-config", str(codex)])

    assert "[mcp_servers.engram]" in codex.read_text()
    assert "# Engram Agent Bootstrap" in capsys.readouterr().out


def test_bootstrap_main_help_uses_bootstrap_program_name(capsys):
    with pytest.raises(SystemExit) as exc:
        bootstrap_main(["--help"])

    assert exc.value.code == 0
    assert "usage: engram-agent-bootstrap" in capsys.readouterr().out


def test_uninstall_codex_config_removes_only_engram_table_and_backs_up(tmp_path):
    config = tmp_path / "config.toml"
    block = codex_toml_block(
        api_url="http://engram.test",
        api_key="me",
        namespace=None,
        python_cmd="python",
    )
    config.write_text(
        'model = "gpt-5"\n\n'
        f"{block}\n"
        "[mcp_servers.other]\n"
        'command = "other"\n'
    )

    result = uninstall_codex_config(config_path=str(config))

    assert result["changed"] is True
    assert result["backup"] is not None
    assert "[mcp_servers.engram]" in (tmp_path / result["backup"]).read_text()
    text = config.read_text()
    assert "[mcp_servers.engram]" not in text
    assert "[mcp_servers.other]" in text
    assert 'model = "gpt-5"' in text


def test_codex_config_backups_do_not_overwrite_within_same_second(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5"\n')
    monkeypatch.setattr("engram.agent_setup.time.strftime", lambda fmt: "20260626235959")

    installed = install_codex_config(
        config_path=str(config),
        api_url="http://engram.test",
        api_key="me",
        namespace=None,
        python_cmd="python",
    )
    uninstalled = uninstall_codex_config(config_path=str(config))

    assert installed["backup"] != uninstalled["backup"]
    assert installed["backup"].endswith("config.toml.engram-bak-20260626235959")
    assert uninstalled["backup"].endswith("config.toml.engram-bak-20260626235959-1")
    assert (tmp_path / installed["backup"]).exists()
    assert (tmp_path / uninstalled["backup"]).exists()
