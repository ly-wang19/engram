"""Generate or install setup snippets for cross-agent Engram memory.

Default mode is intentionally read-only: it prints copy-paste recipes and never mutates a user's
Claude/Codex/Cursor config. Explicit install/uninstall flags can manage Codex's config.toml with a
backup, because Codex is the first adapter we can verify end-to-end with the doctor.
"""
from __future__ import annotations

import argparse
import json
import shutil
import shlex
import time
from pathlib import Path
from typing import Iterable


CLIENTS = ("all", "claude-code", "codex", "cursor", "openai")
DOCTOR_CLIENTS = ("none", "claude-code", "codex", "cursor", "openai")
BOOTSTRAP_TARGETS = ("codex", "mcp-json")
POLICY_START_MARKER = "<!-- ENGRAM MEMORY START -->"
POLICY_END_MARKER = "<!-- ENGRAM MEMORY END -->"


def mcp_args(*, api_url: str | None, api_key: str | None, namespace: str | None) -> list[str]:
    args = ["-m", "engram.mcp"]
    if api_url:
        args += ["--api-url", api_url]
        if api_key:
            args += ["--api-key", api_key]
    else:
        args += ["--namespace", namespace or "me"]
    return args


def _stdio_server(args: list[str], python_cmd: str) -> dict:
    return {"type": "stdio", "command": python_cmd, "args": args}


def _json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _shell(parts: Iterable[str]) -> str:
    return " ".join(shlex.quote(p) for p in parts)


def codex_config_path(path: str | None = None) -> Path:
    return Path(path).expanduser() if path else Path.home() / ".codex" / "config.toml"


def mcp_json_path(path: str | None = None) -> Path:
    return Path(path).expanduser() if path else Path.cwd() / ".mcp.json"


def agents_file_path(path: str | None = None) -> Path:
    return Path(path).expanduser() if path else Path.cwd() / "AGENTS.md"


def codex_toml_block(*, api_url: str | None, api_key: str | None,
                     namespace: str | None, python_cmd: str) -> str:
    args = mcp_args(api_url=api_url, api_key=api_key, namespace=namespace)
    toml_args = ", ".join(json.dumps(a) for a in args)
    return "\n".join([
        "[mcp_servers.engram]",
        f"command = {json.dumps(python_cmd)}",
        f"args = [{toml_args}]",
        "",
    ])


def _toml_header(line: str) -> str:
    return line.split("#", 1)[0].strip()


def _find_toml_table(lines: list[str], header: str) -> tuple[int, int] | None:
    start = None
    for i, line in enumerate(lines):
        if _toml_header(line) == header:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = _toml_header(lines[j])
        if stripped.startswith("[") and stripped.endswith("]"):
            end = j
            break
    return start, end


def _replace_toml_table(text: str, header: str, block: str) -> str:
    lines = text.splitlines(keepends=True)
    span = _find_toml_table(lines, header)
    if span is not None:
        start, end = span
        return "".join(lines[:start]) + block + "".join(lines[end:])
    prefix = text
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix.strip():
        prefix += "\n"
    return prefix + block


def _remove_toml_table(text: str, header: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    span = _find_toml_table(lines, header)
    if span is None:
        return text, False
    start, end = span
    return "".join(lines[:start] + lines[end:]), True


def _backup_path(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d%H%M%S")
    candidate = path.with_name(f"{path.name}.engram-bak-{stamp}")
    if not candidate.exists():
        return candidate
    i = 1
    while True:
        candidate = path.with_name(f"{path.name}.engram-bak-{stamp}-{i}")
        if not candidate.exists():
            return candidate
        i += 1


def agents_policy_block(session_id: str) -> str:
    return "\n".join([
        POLICY_START_MARKER,
        _bootstrap_policy(session_id),
        POLICY_END_MARKER,
        "",
    ])


def _managed_policy_span(text: str) -> tuple[int, int] | None:
    start = text.find(POLICY_START_MARKER)
    end = text.find(POLICY_END_MARKER)
    if start == -1 and end == -1:
        return None
    if start == -1 or end == -1 or end < start:
        raise ValueError("AGENTS.md contains malformed Engram memory policy markers")
    end += len(POLICY_END_MARKER)
    return start, end


def _replace_managed_policy_block(text: str, block: str) -> str:
    span = _managed_policy_span(text)
    if span is not None:
        start, end = span
        return text[:start] + block + text[end:]
    prefix = text
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix.strip():
        prefix += "\n"
    return prefix + block


def _remove_managed_policy_block(text: str) -> tuple[str, bool]:
    span = _managed_policy_span(text)
    if span is None:
        return text, False
    start, end = span
    after = text[:start] + text[end:]
    while "\n\n\n" in after:
        after = after.replace("\n\n\n", "\n\n")
    return after, True


def install_agents_policy(
    *,
    agents_file: str | None = None,
    session_id: str = "codex:super-memory:thread-123",
    dry_run: bool = False,
) -> dict:
    path = agents_file_path(agents_file)
    before = path.read_text() if path.exists() else ""
    block = agents_policy_block(session_id)
    after = _replace_managed_policy_block(before, block)
    changed = after != before
    backup = None
    if changed and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = _backup_path(path)
            shutil.copy2(path, backup)
        path.write_text(after)
    return {
        "ok": True,
        "action": "install",
        "changed": changed,
        "dry_run": dry_run,
        "path": str(path),
        "backup": str(backup) if backup else None,
        "block": block,
    }


def uninstall_agents_policy(*, agents_file: str | None = None, dry_run: bool = False) -> dict:
    path = agents_file_path(agents_file)
    if not path.exists():
        return {
            "ok": True,
            "action": "uninstall",
            "changed": False,
            "dry_run": dry_run,
            "path": str(path),
            "backup": None,
        }
    before = path.read_text()
    after, removed = _remove_managed_policy_block(before)
    backup = None
    if removed and not dry_run:
        backup = _backup_path(path)
        shutil.copy2(path, backup)
        path.write_text(after)
    return {
        "ok": True,
        "action": "uninstall",
        "changed": removed,
        "dry_run": dry_run,
        "path": str(path),
        "backup": str(backup) if backup else None,
    }


def install_codex_config(
    *,
    config_path: str | None = None,
    api_url: str | None = "http://localhost:8000",
    api_key: str | None = "me",
    namespace: str | None = None,
    python_cmd: str = "python",
    dry_run: bool = False,
) -> dict:
    path = codex_config_path(config_path)
    before = path.read_text() if path.exists() else ""
    block = codex_toml_block(
        api_url=api_url,
        api_key=api_key,
        namespace=namespace,
        python_cmd=python_cmd,
    )
    after = _replace_toml_table(before, "[mcp_servers.engram]", block)
    changed = after != before
    backup = None
    if changed and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = _backup_path(path)
            shutil.copy2(path, backup)
        path.write_text(after)
    return {
        "ok": True,
        "action": "install",
        "changed": changed,
        "dry_run": dry_run,
        "path": str(path),
        "backup": str(backup) if backup else None,
        "block": block,
    }


def uninstall_codex_config(*, config_path: str | None = None, dry_run: bool = False) -> dict:
    path = codex_config_path(config_path)
    if not path.exists():
        return {
            "ok": True,
            "action": "uninstall",
            "changed": False,
            "dry_run": dry_run,
            "path": str(path),
            "backup": None,
        }
    before = path.read_text()
    after, removed = _remove_toml_table(before, "[mcp_servers.engram]")
    backup = None
    if removed and not dry_run:
        backup = _backup_path(path)
        shutil.copy2(path, backup)
        path.write_text(after)
    return {
        "ok": True,
        "action": "uninstall",
        "changed": removed,
        "dry_run": dry_run,
        "path": str(path),
        "backup": str(backup) if backup else None,
    }


def _load_json_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def install_mcp_json_config(
    *,
    config_path: str | None = None,
    api_url: str | None = "http://localhost:8000",
    api_key: str | None = "me",
    namespace: str | None = None,
    python_cmd: str = "python",
    server_name: str = "engram",
    dry_run: bool = False,
) -> dict:
    path = mcp_json_path(config_path)
    before = path.read_text() if path.exists() else ""
    data = _load_json_config(path)
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        raise ValueError(f"{path} field `mcpServers` must be a JSON object")
    data["mcpServers"] = servers
    server = _stdio_server(
        mcp_args(api_url=api_url, api_key=api_key, namespace=namespace),
        python_cmd,
    )
    servers[server_name] = server
    after = _json(data) + "\n"
    changed = after != before
    backup = None
    if changed and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = _backup_path(path)
            shutil.copy2(path, backup)
        path.write_text(after)
    return {
        "ok": True,
        "action": "install",
        "changed": changed,
        "dry_run": dry_run,
        "path": str(path),
        "backup": str(backup) if backup else None,
        "server_name": server_name,
        "server": server,
        "config": data,
    }


def uninstall_mcp_json_config(
    *,
    config_path: str | None = None,
    server_name: str = "engram",
    dry_run: bool = False,
) -> dict:
    path = mcp_json_path(config_path)
    if not path.exists():
        return {
            "ok": True,
            "action": "uninstall",
            "changed": False,
            "dry_run": dry_run,
            "path": str(path),
            "backup": None,
            "server_name": server_name,
        }
    data = _load_json_config(path)
    servers = data.get("mcpServers")
    removed = isinstance(servers, dict) and server_name in servers
    if removed:
        del servers[server_name]
    before = path.read_text()
    after = _json(data) + "\n"
    backup = None
    if removed and not dry_run:
        backup = _backup_path(path)
        shutil.copy2(path, backup)
        path.write_text(after)
    return {
        "ok": True,
        "action": "uninstall",
        "changed": removed,
        "dry_run": dry_run,
        "path": str(path),
        "backup": str(backup) if backup else None,
        "server_name": server_name,
        "config": data,
        "before": before if dry_run else None,
    }


def run_codex_doctor(
    *,
    python_cmd: str = "python",
    api_url: str | None = None,
    api_key: str = "me",
    codex_config: str | None = None,
    mcp_json: str | None = None,
    mcp_server_name: str = "engram",
) -> dict:
    from .agent_doctor import diagnose

    return diagnose(
        client="codex",
        python_cmd=python_cmd,
        api_url=api_url,
        api_key=api_key,
        codex_config=codex_config,
        mcp_json=mcp_json,
        mcp_server_name=mcp_server_name,
    )


def run_mcp_doctor(
    *,
    client: str = "none",
    python_cmd: str = "python",
    api_url: str | None = None,
    api_key: str = "me",
    mcp_json: str | None = None,
    mcp_server_name: str = "engram",
) -> dict:
    from .agent_doctor import diagnose

    return diagnose(
        client=client,
        python_cmd=python_cmd,
        api_url=api_url,
        api_key=api_key,
        mcp_json=mcp_json,
        mcp_server_name=mcp_server_name,
    )


def _doctor_verify_command(
    *,
    client: str,
    python_cmd: str,
    api_url: str | None,
    api_key: str | None,
) -> list[str]:
    verify = ["engram-agent-doctor", "--client", client, "--python", python_cmd]
    if api_url:
        verify += ["--api-url", api_url]
        if api_key:
            verify += ["--api-key", api_key]
    elif api_key:
        verify += ["--api-key", api_key]
    return verify


def render_codex_config_result(
    result: dict,
    *,
    python_cmd: str = "python",
    api_url: str | None = None,
    api_key: str | None = None,
    doctor_report: dict | None = None,
    doctor_skipped: str | None = None,
) -> str:
    action = "install" if result.get("action") == "install" else "uninstall"
    verb = {
        ("install", True): "Would install",
        ("install", False): "Installed",
        ("uninstall", True): "Would uninstall",
        ("uninstall", False): "Uninstalled",
    }[(action, bool(result.get("dry_run")))]
    if not result.get("changed"):
        verb = "No changes needed"
    lines = [
        "# Engram Codex Config",
        "",
        f"{verb}: `{result.get('path')}`",
    ]
    if result.get("backup"):
        lines.append(f"Backup: `{result['backup']}`")
    if action == "install" and result.get("block"):
        lines += ["", "Configured block:", "", "```toml", result["block"].rstrip(), "```"]
    if action == "install":
        verify = _doctor_verify_command(
            client="codex",
            python_cmd=python_cmd,
            api_url=api_url,
            api_key=api_key,
        )
        lines += [
            "",
            "Verify with:",
            "",
            "```bash",
            _shell(verify),
            "```",
        ]
    if doctor_skipped:
        lines += ["", f"Doctor skipped: {doctor_skipped}"]
    if doctor_report is not None:
        from .agent_doctor import render_report

        lines += ["", "Doctor result:", "", render_report(doctor_report).rstrip()]
    return "\n".join(lines).rstrip() + "\n"


def render_mcp_json_config_result(
    result: dict,
    *,
    python_cmd: str = "python",
    doctor_client: str = "none",
    api_url: str | None = None,
    api_key: str | None = None,
    doctor_report: dict | None = None,
    doctor_skipped: str | None = None,
) -> str:
    action = "install" if result.get("action") == "install" else "uninstall"
    verb = {
        ("install", True): "Would install",
        ("install", False): "Installed",
        ("uninstall", True): "Would uninstall",
        ("uninstall", False): "Uninstalled",
    }[(action, bool(result.get("dry_run")))]
    if not result.get("changed"):
        verb = "No changes needed"
    lines = [
        "# Engram MCP JSON Config",
        "",
        f"{verb}: `{result.get('path')}`",
    ]
    if result.get("backup"):
        lines.append(f"Backup: `{result['backup']}`")
    if action == "install" and result.get("server"):
        lines += [
            "",
            f"Configured server `{result.get('server_name', 'engram')}`:",
            "",
            "```json",
            _json(result["server"]),
            "```",
        ]
        verify = _doctor_verify_command(
            client=doctor_client,
            python_cmd=python_cmd,
            api_url=api_url,
            api_key=api_key,
        )
        lines += ["", "Verify with:", "", "```bash", _shell(verify), "```"]
    if doctor_skipped:
        lines += ["", f"Doctor skipped: {doctor_skipped}"]
    if doctor_report is not None:
        from .agent_doctor import render_report

        lines += ["", "Doctor result:", "", render_report(doctor_report).rstrip()]
    return "\n".join(lines).rstrip() + "\n"


def render_agents_policy_result(result: dict) -> str:
    action = "install" if result.get("action") == "install" else "uninstall"
    verb = {
        ("install", True): "Would install",
        ("install", False): "Installed",
        ("uninstall", True): "Would uninstall",
        ("uninstall", False): "Uninstalled",
    }[(action, bool(result.get("dry_run")))]
    if not result.get("changed"):
        verb = "No changes needed"
    lines = [
        "# Engram AGENTS.md Policy",
        "",
        f"{verb}: `{result.get('path')}`",
    ]
    if result.get("backup"):
        lines.append(f"Backup: `{result['backup']}`")
    if action == "install" and result.get("block"):
        lines += ["", "Managed policy block:", "", "```text", result["block"].rstrip(), "```"]
    return "\n".join(lines).rstrip() + "\n"


def _policy(agent: str, session_id: str) -> str:
    return f"""Use Engram as the user's cross-agent long-term memory.
At session start or when debugging memory wiring, call engram_agent_status with session_id `{session_id}`.
Before tasks that may depend on prior user/project context, call engram_recall with session_id `{session_id}`.
When the user states a durable preference, project rule, decision, or reusable fact, call engram_remember with the same session_id and scope `long` or `auto`.
For short-lived state that matters only inside the current task, call engram_remember with scope `working`.
When the user asks to correct or delete a memory, call engram_list_facts to find the fact id, then call engram_update_fact or engram_delete_fact with confirm=true.
When the user asks to export their memory, call engram_export(response_format="json"); set include_sensitive=true only for an explicit private export.
When the user asks you to pay more attention to or suppress a class of memories, call engram_set_focus; use engram_get_focus to inspect the current policy.
When the user asks what was saved from a thread, call engram_session_report for that session.
When the thread ends or you switch tasks, call engram_close_session with the same session_id.
Do not store secrets, credentials, large logs, or short-lived state as durable memory.
For this client, use session IDs shaped like `{agent}:<project>:<thread>`."""


def _bootstrap_policy(session_id: str) -> str:
    return f"""Use Engram as the user's cross-agent long-term memory.
At session start or when debugging memory wiring, call engram_agent_status with a session_id like `{session_id}`.
Before tasks that may depend on prior user/project context, call engram_recall with a session_id like `{session_id}`.
When the user states a durable preference, project rule, decision, or reusable fact, call engram_remember with the same session_id and scope `long` or `auto`.
For short-lived state that matters only inside the current task, call engram_remember with scope `working`.
When the user asks to correct or delete a memory, call engram_list_facts to find the fact id, then call engram_update_fact or engram_delete_fact with confirm=true.
When the user asks to export their memory, call engram_export(response_format="json"); set include_sensitive=true only for an explicit private export.
When the user asks you to pay more attention to or suppress a class of memories, call engram_set_focus; use engram_get_focus to inspect the current policy.
When the user asks what was saved from a thread, call engram_session_report for that session.
When the thread ends or you switch tasks, call engram_close_session with the same session_id.
Do not store secrets, credentials, large logs, or short-lived state as durable memory.
Use session IDs shaped like `<agent>:<project>:<thread>`, for example `codex:<repo>:<thread>` or `claude-code:<repo>:<thread>`."""


def parse_bootstrap_targets(value: str) -> list[str]:
    targets = [p.strip() for p in value.split(",") if p.strip()]
    if not targets:
        raise ValueError("bootstrap targets cannot be empty")
    invalid = [t for t in targets if t not in BOOTSTRAP_TARGETS]
    if invalid:
        raise ValueError(f"unknown bootstrap target(s): {', '.join(invalid)}")
    # Stable unique order so repeated flags do not duplicate work.
    return [t for t in BOOTSTRAP_TARGETS if t in set(targets)]


def _action_verb(result: dict) -> str:
    action = "install" if result.get("action") == "install" else "uninstall"
    verb = {
        ("install", True): "would install",
        ("install", False): "installed",
        ("uninstall", True): "would uninstall",
        ("uninstall", False): "uninstalled",
    }[(action, bool(result.get("dry_run")))]
    return "no changes needed" if not result.get("changed") else verb


def render_bootstrap_result(
    *,
    results: list[tuple[str, dict]],
    python_cmd: str,
    api_url: str | None,
    api_key: str | None,
    session_id: str,
    doctor_reports: list[dict] | None = None,
    doctor_skipped: str | None = None,
) -> str:
    lines = ["# Engram Agent Bootstrap", ""]
    policy_result = None
    for label, result in results:
        line = f"- {label}: {_action_verb(result)} `{result.get('path')}`"
        if result.get("backup"):
            line += f" (backup `{result['backup']}`)"
        lines.append(line)
        if label == "AGENTS.md policy":
            policy_result = result
    if doctor_skipped:
        lines += ["", f"Doctor skipped: {doctor_skipped}"]
    for report in doctor_reports or []:
        from .agent_doctor import render_report

        lines += ["", "Doctor result:", "", render_report(report).rstrip()]
    verify = _doctor_verify_command(
        client="codex" if any(label == "Codex" for label, _ in results) else "none",
        python_cmd=python_cmd,
        api_url=api_url,
        api_key=api_key,
    )
    lines += ["", "Verify later with:", "", "```bash", _shell(verify), "```", ""]
    if policy_result is not None:
        lines += [
            "Managed AGENTS.md policy block:",
            "",
            "```text",
            policy_result.get("block", agents_policy_block(session_id)).rstrip(),
            "```",
        ]
    else:
        lines += [
            "Suggested AGENTS.md note:",
            "",
            "```text",
            _bootstrap_policy(session_id),
            "```",
        ]
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(
    client: str = "all",
    *,
    api_url: str | None = "http://localhost:8000",
    api_key: str | None = "me",
    namespace: str | None = None,
    session_id: str = "codex:super-memory:thread-123",
    python_cmd: str = "python",
) -> str:
    args = mcp_args(api_url=api_url, api_key=api_key, namespace=namespace)
    server = _stdio_server(args, python_cmd)
    out: list[str] = [
        "# Engram Agent Setup",
        "",
        "Shared lifecycle:",
        "",
        "```text",
        "recall before work -> remember durable information -> close the session",
        "```",
        "",
    ]

    if client in {"all", "claude-code"}:
        add_json = _json(server)
        out += [
            "## Claude Code",
            "",
            "CLI:",
            "",
            "```bash",
            f"claude mcp add-json engram {_shell([add_json])}",
            "```",
            "",
            "Project `.mcp.json`:",
            "",
            "```json",
            _json({"mcpServers": {"engram": server}}),
            "```",
            "",
            "Suggested instruction:",
            "",
            "```text",
            _policy("claude-code", session_id.replace("codex:", "claude-code:", 1)),
            "```",
            "",
        ]

    if client in {"all", "codex"}:
        cli = ["codex", "mcp", "add", "engram", "--", python_cmd, *args]
        out += [
            "## Codex",
            "",
            "CLI:",
            "",
            "```bash",
            _shell(cli),
            "```",
            "",
            "`~/.codex/config.toml`:",
            "",
            "```toml",
            codex_toml_block(
                api_url=api_url,
                api_key=api_key,
                namespace=namespace,
                python_cmd=python_cmd,
            ).rstrip(),
            "```",
            "",
            "Suggested AGENTS.md note:",
            "",
            "```text",
            _policy("codex", session_id),
            "```",
            "",
        ]

    if client in {"all", "cursor"}:
        out += [
            "## Cursor / Generic MCP Client",
            "",
            "MCP JSON:",
            "",
            "```json",
            _json({"mcpServers": {"engram": server}}),
            "```",
            "",
            "Suggested instruction:",
            "",
            "```text",
            _policy("cursor", session_id.replace("codex:", "cursor:", 1)),
            "```",
            "",
        ]

    if client in {"all", "openai"}:
        key = api_key or namespace or "me"
        url = (api_url or "http://localhost:8000").rstrip("/") + "/v1"
        out += [
            "## OpenAI-Compatible Apps",
            "",
            "Python:",
            "",
            "```python",
            "from openai import OpenAI",
            "",
            f"session_id = {session_id!r}",
            f"client = OpenAI(base_url={url!r}, api_key={key!r})",
            "client.chat.completions.create(",
            "    model='engram',",
            "    messages=[{'role': 'user', 'content': 'Continue the work.'}],",
            "    extra_body={'memory': {",
            "        'session_id': session_id,",
            "        'recall': True,",
            "        'remember': True,",
            "        'scope': 'auto',",
            "    }},",
            ")",
            "```",
            "",
            "Close the thread:",
            "",
            "```bash",
            _shell([
                "curl", "-s", "-X", "POST", (api_url or "http://localhost:8000").rstrip("/") + "/v1/sessions/close",
                "-H", f"Authorization: Bearer {key}",
                "-H", "Content-Type: application/json",
                "-d", json.dumps({"session_id": session_id}),
            ]),
            "```",
            "",
        ]
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None, *, prog: str = "engram-agent-setup") -> None:
    ap = argparse.ArgumentParser(
        prog=prog,
        description="Print or explicitly install config snippets for using Engram as cross-agent memory.",
    )
    ap.add_argument("--client", choices=CLIENTS, default="all", help="which adapter recipe to print")
    ap.add_argument("--api-url", default="http://localhost:8000",
                    help="Engram HTTP server for remote MCP/OpenAI-compatible mode")
    ap.add_argument("--api-key", default="me", help="Bearer key = user memory namespace")
    ap.add_argument("--local", action="store_true", help="use local MCP storage instead of an HTTP server")
    ap.add_argument("--namespace", default="me", help="local MCP namespace when --local is set")
    ap.add_argument("--session-id", default="codex:super-memory:thread-123",
                    help="example session id to show in generated snippets")
    ap.add_argument("--python", default="python",
                    help="Python executable that has engram-memory[mcp] installed")
    ap.add_argument("--bootstrap", action="store_true",
                    help="install Codex + .mcp.json configs, run doctor, and print AGENTS.md guidance")
    ap.add_argument("--bootstrap-targets", default="codex,mcp-json",
                    help="comma-separated bootstrap targets: codex,mcp-json (default both)")
    ap.add_argument("--install-codex", action="store_true",
                    help="write/update [mcp_servers.engram] in Codex config.toml (backs up first)")
    ap.add_argument("--uninstall-codex", action="store_true",
                    help="remove [mcp_servers.engram] from Codex config.toml (backs up first)")
    ap.add_argument("--codex-config", default=None,
                    help="Codex config path (default ~/.codex/config.toml)")
    ap.add_argument("--install-mcp-json", action="store_true",
                    help="write/update an `engram` server in a project .mcp.json (backs up first)")
    ap.add_argument("--uninstall-mcp-json", action="store_true",
                    help="remove the `engram` server from a project .mcp.json (backs up first)")
    ap.add_argument("--mcp-json", default=None,
                    help="MCP JSON path (default ./.mcp.json)")
    ap.add_argument("--mcp-server-name", default="engram",
                    help="server name to manage inside mcpServers (default engram)")
    ap.add_argument("--install-policy", action="store_true",
                    help="write/update a managed Engram memory block in AGENTS.md (backs up first)")
    ap.add_argument("--uninstall-policy", action="store_true",
                    help="remove only the managed Engram memory block from AGENTS.md (backs up first)")
    ap.add_argument("--agents-file", default=None,
                    help="AGENTS.md path for --install-policy/--uninstall-policy (default ./AGENTS.md)")
    ap.add_argument("--doctor-client", choices=DOCTOR_CLIENTS, default="none",
                    help="client to pass to --doctor for --install-mcp-json (e.g. claude-code or cursor)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would change without writing files")
    ap.add_argument("--doctor", action="store_true",
                    help="after installing config, run engram-agent-doctor against the same Python")
    ap.add_argument("--no-doctor", action="store_true",
                    help="with --bootstrap, skip the automatic doctor check")
    args = ap.parse_args(argv)
    actions = [
        args.bootstrap,
        args.install_codex,
        args.uninstall_codex,
        args.install_mcp_json,
        args.uninstall_mcp_json,
        args.uninstall_policy,
    ]
    if sum(bool(a) for a in actions) > 1:
        ap.error("choose only one install/uninstall action")
    if args.install_policy and args.uninstall_policy:
        ap.error("choose only one of --install-policy or --uninstall-policy")
    if args.install_policy and not args.bootstrap and any([
        args.install_codex,
        args.uninstall_codex,
        args.install_mcp_json,
        args.uninstall_mcp_json,
    ]):
        ap.error("--install-policy can be used by itself or with --bootstrap")
    if args.doctor and not (args.bootstrap or args.install_codex or args.install_mcp_json):
        ap.error("--doctor requires --bootstrap, --install-codex, or --install-mcp-json")
    if args.no_doctor and not args.bootstrap:
        ap.error("--no-doctor requires --bootstrap")

    api_url = None if args.local else args.api_url
    api_key = None if args.local else args.api_key
    namespace = args.namespace if args.local else None
    if args.bootstrap:
        try:
            targets = parse_bootstrap_targets(args.bootstrap_targets)
        except ValueError as exc:
            ap.error(str(exc))
        results: list[tuple[str, dict]] = []
        if "codex" in targets:
            results.append((
                "Codex",
                install_codex_config(
                    config_path=args.codex_config,
                    api_url=api_url,
                    api_key=api_key,
                    namespace=namespace,
                    python_cmd=args.python,
                    dry_run=args.dry_run,
                ),
            ))
        if "mcp-json" in targets:
            results.append((
                "MCP JSON",
                install_mcp_json_config(
                    config_path=args.mcp_json,
                    api_url=api_url,
                    api_key=api_key,
                    namespace=namespace,
                    python_cmd=args.python,
                    server_name=args.mcp_server_name,
                    dry_run=args.dry_run,
                ),
            ))
        if args.install_policy:
            results.append((
                "AGENTS.md policy",
                install_agents_policy(
                    agents_file=args.agents_file,
                    session_id=args.session_id,
                    dry_run=args.dry_run,
                ),
            ))
        doctor_reports: list[dict] = []
        doctor_skipped = None
        if args.dry_run:
            doctor_skipped = "dry-run does not claim installed agent configs are ready"
        elif args.no_doctor:
            doctor_skipped = "--no-doctor was set"
        elif "codex" in targets:
            codex_path = next((r["path"] for label, r in results if label == "Codex"), None)
            mcp_json_path_ = next((r["path"] for label, r in results if label == "MCP JSON"), None)
            doctor_reports.append(run_codex_doctor(
                python_cmd=args.python,
                api_url=api_url,
                api_key=api_key or args.namespace,
                codex_config=codex_path,
                mcp_json=mcp_json_path_,
                mcp_server_name=args.mcp_server_name,
            ))
            if args.doctor_client not in {"none", "codex"}:
                doctor_reports.append(run_mcp_doctor(
                    client=args.doctor_client,
                    python_cmd=args.python,
                    api_url=api_url,
                    api_key=api_key or args.namespace,
                    mcp_json=mcp_json_path_,
                    mcp_server_name=args.mcp_server_name,
                ))
        elif "mcp-json" in targets:
            mcp_json_path_ = next((r["path"] for label, r in results if label == "MCP JSON"), None)
            doctor_reports.append(run_mcp_doctor(
                client=args.doctor_client,
                python_cmd=args.python,
                api_url=api_url,
                api_key=api_key or args.namespace,
                mcp_json=mcp_json_path_,
                mcp_server_name=args.mcp_server_name,
            ))
        print(
            render_bootstrap_result(
                results=results,
                python_cmd=args.python,
                api_url=api_url,
                api_key=api_key or namespace,
                session_id=args.session_id,
                doctor_reports=doctor_reports,
                doctor_skipped=doctor_skipped,
            ),
            end="",
        )
        if any(not r.get("ok") for r in doctor_reports):
            raise SystemExit(1)
        return
    if args.install_codex:
        result = install_codex_config(
            config_path=args.codex_config,
            api_url=api_url,
            api_key=api_key,
            namespace=namespace,
            python_cmd=args.python,
            dry_run=args.dry_run,
        )
        doctor_report = None
        doctor_skipped = None
        if args.doctor and args.dry_run:
            doctor_skipped = "dry-run does not claim the installed Codex config is ready"
        elif args.doctor:
            doctor_report = run_codex_doctor(
                python_cmd=args.python,
                api_url=api_url,
                api_key=api_key or args.namespace,
                codex_config=result["path"],
            )
        print(
            render_codex_config_result(
                result,
                python_cmd=args.python,
                api_url=api_url,
                api_key=api_key or namespace,
                doctor_report=doctor_report,
                doctor_skipped=doctor_skipped,
            ),
            end="",
        )
        if doctor_report is not None and not doctor_report.get("ok"):
            raise SystemExit(1)
        return
    if args.uninstall_codex:
        result = uninstall_codex_config(config_path=args.codex_config, dry_run=args.dry_run)
        print(render_codex_config_result(result, python_cmd=args.python), end="")
        return
    if args.install_mcp_json:
        result = install_mcp_json_config(
            config_path=args.mcp_json,
            api_url=api_url,
            api_key=api_key,
            namespace=namespace,
            python_cmd=args.python,
            server_name=args.mcp_server_name,
            dry_run=args.dry_run,
        )
        doctor_report = None
        doctor_skipped = None
        if args.doctor and args.dry_run:
            doctor_skipped = "dry-run does not claim the installed MCP JSON config is ready"
        elif args.doctor:
            doctor_report = run_mcp_doctor(
                client=args.doctor_client,
                python_cmd=args.python,
                api_url=api_url,
                api_key=api_key or args.namespace,
                mcp_json=result["path"],
                mcp_server_name=args.mcp_server_name,
            )
        print(
            render_mcp_json_config_result(
                result,
                python_cmd=args.python,
                doctor_client=args.doctor_client,
                api_url=api_url,
                api_key=api_key or namespace,
                doctor_report=doctor_report,
                doctor_skipped=doctor_skipped,
            ),
            end="",
        )
        if doctor_report is not None and not doctor_report.get("ok"):
            raise SystemExit(1)
        return
    if args.uninstall_mcp_json:
        result = uninstall_mcp_json_config(
            config_path=args.mcp_json,
            server_name=args.mcp_server_name,
            dry_run=args.dry_run,
        )
        print(render_mcp_json_config_result(result, python_cmd=args.python), end="")
        return
    if args.install_policy:
        result = install_agents_policy(
            agents_file=args.agents_file,
            session_id=args.session_id,
            dry_run=args.dry_run,
        )
        print(render_agents_policy_result(result), end="")
        return
    if args.uninstall_policy:
        result = uninstall_agents_policy(agents_file=args.agents_file, dry_run=args.dry_run)
        print(render_agents_policy_result(result), end="")
        return
    print(render_markdown(
        args.client,
        api_url=api_url,
        api_key=api_key,
        namespace=namespace,
        session_id=args.session_id,
        python_cmd=args.python,
    ), end="")


def bootstrap_main(argv: list[str] | None = None) -> None:
    import sys

    main(["--bootstrap", *(sys.argv[1:] if argv is None else argv)], prog="engram-agent-bootstrap")


if __name__ == "__main__":
    main()
