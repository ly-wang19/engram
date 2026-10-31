"""watch reads the user's own agent sessions: a delegate app's per-chat Codex threads (other people's
messages) are skipped by their working directory, and CODEX_HOME moves where Codex sessions are found."""
from __future__ import annotations

import json
import os

from engram.connectors import agent_sessions


def _rollout(path, cwd: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"type": "session_meta", "payload": {"id": "x", "cwd": cwd, "originator": "Codex Desktop"}}]
    rows += [{"type": "response_item", "payload": {"type": "message", "role": "user",
                                                    "content": [{"type": "input_text", "text": "hi " * 1000}]}}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(path)


def test_delegate_chat_threads_are_excluded_by_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_WATCH_EXCLUDE_CWD", raising=False)
    root = tmp_path / "sessions"
    mine = _rollout(root / "2026/10/31/rollout-a.jsonl", "/Users/me/Projects/engram")
    group = _rollout(root / "2026/10/31/rollout-b.jsonl",
                     "/Users/me/AI work/org-a/conversations/group-0123456789abcdef0123")
    o2o = _rollout(root / "2026/10/31/rollout-c.jsonl",
                   "C:\\Users\\me\\Ruwo\\org-b\\conversations\\o2o-fedcba9876543210fedc")
    found = agent_sessions.find_sessions(root=str(root))
    assert found == [mine] and group not in found and o2o not in found
    assert set(agent_sessions.find_sessions(root=str(root), exclude_cwd=None)) == {mine, group, o2o}


def test_exclusion_pattern_is_configurable_and_can_be_disabled(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    mine = _rollout(root / "rollout-a.jsonl", "/work/secret-project")
    monkeypatch.setenv("ENGRAM_WATCH_EXCLUDE_CWD", r"/secret-")
    assert agent_sessions.find_sessions(root=str(root)) == []
    monkeypatch.setenv("ENGRAM_WATCH_EXCLUDE_CWD", "")
    assert agent_sessions.find_sessions(root=str(root)) == [mine]


def test_codex_home_moves_the_codex_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    roots = agent_sessions.session_roots()
    assert os.path.join(str(tmp_path / "codex-home"), "sessions") in roots
    monkeypatch.delenv("CODEX_HOME")
    assert os.path.join(os.path.expanduser("~/.codex"), "sessions") in agent_sessions.session_roots()
