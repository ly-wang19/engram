from __future__ import annotations

import json

import pytest

from examples.cross_agent_lifecycle import (
    VERIFY_PHRASE,
    exercise_lifecycle,
    local_call,
    main,
)


def test_cross_agent_lifecycle_example_local_mode(tmp_path):
    lines: list[str] = []
    result = exercise_lifecycle(
        local_call("me", str(tmp_path)),
        namespace="me",
        agent="codex",
        project="super-memory",
        thread="codex-smoke",
        output=lines.append,
    )

    assert result["found"] is True
    assert result["status_before"]["ok"] is True
    assert result["status_before"]["session"]["episodes"] == 0
    assert VERIFY_PHRASE in result["context_after"]
    assert result["status_after_write"]["session"]["episodes"] == 3
    assert result["status_after_write"]["session"]["working_live"] == 1
    assert result["closed"]["ok"] is True
    assert result["closed"]["working_cleared"] == 1
    assert result["report"]["episodes"] == 3
    assert len(result["report"]["facts"]) == 2
    assert any(VERIFY_PHRASE in fact["text"] for fact in result["report"]["facts"])
    assert any("PASS: lifecycle saved durable memory" in line for line in lines)

    status_payload = json.dumps(result["status_after_write"], ensure_ascii=False)
    assert VERIFY_PHRASE not in status_payload
    assert "Current task state" not in status_payload


def test_cross_agent_lifecycle_main_local_mode(tmp_path, capsys):
    main([
        "--local",
        "--data-dir",
        str(tmp_path),
        "--key",
        "me",
        "--project",
        "super-memory",
        "--thread",
        "codex-smoke",
    ])

    out = capsys.readouterr().out
    assert "mode: local" in out
    assert f"data_dir: {tmp_path}" in out
    assert "agent_status:" in out
    assert "session_report: episodes=3 facts=2 redacted=0" in out
    assert "PASS: lifecycle saved durable memory and the session report audits it." in out
    assert "PASS: close_session cleared this session's working memory." in out


def test_cross_agent_lifecycle_main_exits_when_verify_fails(tmp_path, monkeypatch):
    def empty_local_call(key: str, data_dir: str):
        call = local_call(key, data_dir)

        def wrapper(path: str, body: dict | None = None, method: str = "POST") -> dict:
            if path == "/v1/recall":
                return {"context": ""}
            if path.startswith("/v1/sessions/report"):
                return {"episodes": 3, "facts": [], "redacted": 0}
            return call(path, body, method)

        return wrapper

    monkeypatch.setattr("examples.cross_agent_lifecycle.local_call", empty_local_call)

    with pytest.raises(SystemExit) as exc:
        main(["--local", "--data-dir", str(tmp_path)])

    assert exc.value.code == 1
