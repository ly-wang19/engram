"""Ingesting Claude Code / Codex session logs — the path that was missing.

The owner's memory-worthy material IS his agent sessions (1600+ Claude Code transcripts and 1200+ Codex
ones sit on his disk), but there was no way in: the generic JSONL reader kept the tool calls and lost the
conversation, so in practice almost nothing was ever stored. These tests pin what gets kept and, just as
importantly, what gets thrown away."""
from __future__ import annotations

import json
import os

from engram.connectors import parse, sniff
from engram.connectors.agent_sessions import parse_agent_session


def _cc(rows: list[dict]) -> str:
    return "\n".join(json.dumps(r) for r in rows)


def test_keeps_the_conversation_and_drops_the_machinery():
    log = _cc([
        {"type": "user", "timestamp": "2026-08-01T10:00:00Z",
         "message": {"role": "user", "content": "I moved the project to Rust for the hot path."}},
        {"type": "assistant", "timestamp": "2026-08-01T10:00:05Z",
         "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "let me consider"}]}},
        {"type": "assistant", "timestamp": "2026-08-01T10:00:09Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Rust for the hot path makes sense."}]}},
        {"type": "assistant", "timestamp": "2026-08-01T10:00:10Z",
         "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
        {"type": "user", "timestamp": "2026-08-01T10:00:11Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "content": "3 matches"}]}},
        {"type": "queue-operation", "operation": "enqueue", "content": "routing preamble"},
        {"type": "ai-title", "title": "Rust migration"},
    ])
    sessions = parse_agent_session(log, session_id="s1")
    assert len(sessions) == 1
    bodies = [m.content for m in sessions[0].messages]
    assert bodies == ["I moved the project to Rust for the hot path.",
                      "Rust for the hot path makes sense."], bodies
    # thinking is excluded on purpose: it is reasoning the agent may have discarded
    assert not any("consider" in b for b in bodies)
    assert sessions[0].messages[0].event_time is not None  # timestamps survive for the bi-temporal layer


def test_short_turns_and_tool_echoes_are_dropped():
    log = _cc([
        {"type": "user", "message": {"role": "user", "content": "ok"}},
        {"type": "user", "message": {"role": "user", "content": "<tool_use_result>done</tool_use_result>"}},
        {"type": "user", "message": {"role": "user", "content": "My daughter started at Tsinghua this year."}},
    ])
    bodies = [m.content for m in parse_agent_session(log)[0].messages]
    assert bodies == ["My daughter started at Tsinghua this year."]


def test_sub_agent_turns_are_excluded_by_default():
    log = _cc([
        {"type": "user", "message": {"role": "user", "content": "Please refactor the parser module."}},
        {"type": "assistant", "isSidechain": True,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Sub-agent internal chatter here."}]}},
    ])
    assert len(parse_agent_session(log)[0].messages) == 1
    assert len(parse_agent_session(log, include_sidechains=True)[0].messages) == 2


def test_codex_rollout_format_is_understood():
    """Codex wraps turns as response_item/payload rather than Claude Code's `message`."""
    log = _cc([
        {"type": "session_meta", "payload": {"id": "x"}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer",
                                              "content": [{"type": "input_text", "text": "<permissions instructions>"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "Ship the release notes today."}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                              "content": [{"type": "output_text", "text": "Release notes are drafted."}]}},
    ])
    bodies = [m.content for m in parse_agent_session(log)[0].messages]
    # the developer turn is harness instructions, not the user's history
    assert bodies == ["Ship the release notes today.", "Release notes are drafted."], bodies


def test_sniff_routes_agent_logs_away_from_the_generic_jsonl_reader():
    """Without this the generic reader silently keeps tool calls and loses the conversation."""
    cc = _cc([
        {"type": "user", "isSidechain": False, "message": {"role": "user", "content": "Where did we land on pricing?"}},
        {"type": "assistant", "isSidechain": False,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "We landed on usage-based."}]}},
    ])
    assert sniff(cc) == "agent_session"
    bodies = [m.content for m in parse(cc)[0].messages]
    assert "We landed on usage-based." in bodies


def test_an_all_machinery_session_yields_nothing_rather_than_junk():
    log = _cc([
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
        {"type": "queue-operation", "operation": "enqueue"},
    ])
    assert parse_agent_session(log) == []


class TestSecretRedaction:
    """Agent sessions routinely contain live credentials — the user pastes a key, a command echoes an
    .env. Long-term memory is the worst place for one: durable, retrievable by every connected agent,
    and exportable. Redaction happens at ingest because a secret that reaches the store has already
    leaked into embeddings, summaries and extracted facts."""

    def test_common_credential_shapes_are_redacted(self):
        from engram.connectors.agent_sessions import redact_secrets

        for raw in [
            "DEEPSEEK_API_KEY=sk-EXAMPLENOTAREALKEY000000000000",
            "export ARK_API_KEY=abc123def456ghi789",
            "token: ghp_abcdefghijklmnopqrstuvwxyz1234",
            "AKIA1234567890ABCDEF",
        ]:
            assert "[REDACTED]" in redact_secrets(raw), raw
            assert "EXAMPLENOTAREAL" not in redact_secrets(raw)

    def test_ordinary_technical_prose_is_untouched(self):
        """Over-redaction would quietly mangle the memory it is protecting."""
        from engram.connectors.agent_sessions import redact_secrets

        for raw in [
            "我们决定用 bge-small 作为 embedder",
            "The extractor is volcano:doubao-seed-1-6-flash-250615",
            "engram_lean scored 84.4% vs full_context 78.8%",
        ]:
            assert redact_secrets(raw) == raw, raw

    def test_redaction_happens_during_parsing_not_after(self):
        from engram.connectors.agent_sessions import parse_agent_session

        log = json.dumps({"type": "user", "message": {
            "role": "user", "content": "Here is the key: sk-abcdefghijklmnopqrstuvwxyz012345"}})
        body = parse_agent_session(log)[0].messages[0].content
        assert "[REDACTED]" in body and "sk-abcdef" not in body


class TestWatcher:
    def test_a_live_session_is_skipped_until_it_goes_quiet(self, tmp_path, monkeypatch):
        """A transcript still being written is a moving target; ingesting it now only forces a
        re-ingest later with the tail attached."""
        from engram.connectors import watch

        live = tmp_path / "live.jsonl"
        live.write_text("x" * 4096)
        monkeypatch.setattr(watch, "find_sessions", lambda **kw: [str(live)])

        now = os.path.getmtime(live) + 60           # written a minute ago
        assert watch.pending_sessions({}, now=now, quiet_seconds=900) == []
        later = os.path.getmtime(live) + 3600       # an hour later, it has gone quiet
        assert watch.pending_sessions({}, now=later, quiet_seconds=900) == [str(live)]

    def test_unchanged_sessions_are_not_reingested(self, tmp_path, monkeypatch):
        from engram.connectors import watch

        f = tmp_path / "done.jsonl"
        f.write_text("y" * 4096)
        monkeypatch.setattr(watch, "find_sessions", lambda **kw: [str(f)])
        later = os.path.getmtime(f) + 3600

        state = {"seen": {str(f): f.stat().st_size}}
        assert watch.pending_sessions(state, now=later, quiet_seconds=900) == []
        f.write_text("y" * 8192)  # the session continued
        assert watch.pending_sessions(state, now=later, quiet_seconds=900) == [str(f)]
