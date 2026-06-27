from __future__ import annotations

import pytest

from examples.cross_agent_handoff import exercise_handoff, local_call, main


def test_cross_agent_handoff_example_local_mode(tmp_path):
    lines: list[str] = []
    result = exercise_handoff(
        local_call("me", str(tmp_path)),
        namespace="me",
        project="super-memory",
        output=lines.append,
    )

    assert result["found"] is True
    assert result["remembered"]["ok"] is True
    assert result["closed_source"]["ok"] is True
    assert result["closed_target"]["ok"] is True
    assert "committed eval logs" in result["context"]
    assert result["source_session"] in result["context"]
    assert any("PASS: target agent recalled" in line for line in lines)


def test_cross_agent_handoff_main_local_mode(tmp_path, capsys):
    main(["--local", "--data-dir", str(tmp_path), "--key", "me", "--project", "super-memory"])

    out = capsys.readouterr().out
    assert "mode: local" in out
    assert f"data_dir: {tmp_path}" in out
    assert "PASS: target agent recalled the source agent's durable memory." in out


def test_cross_agent_handoff_main_exits_when_verify_fails(tmp_path, monkeypatch):
    def empty_local_call(key: str, data_dir: str):
        call = local_call(key, data_dir)

        def wrapper(path: str, body: dict | None = None, method: str = "POST") -> dict:
            if path == "/v1/recall":
                return {"context": ""}
            return call(path, body, method)

        return wrapper

    monkeypatch.setattr("examples.cross_agent_handoff.local_call", empty_local_call)

    with pytest.raises(SystemExit) as exc:
        main(["--local", "--data-dir", str(tmp_path)])

    assert exc.value.code == 1
