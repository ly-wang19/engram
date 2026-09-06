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


def test_watcher_closes_each_session_so_it_gets_distilled(monkeypatch):
    """Importing alone only stores transcripts. The distillation into decisions/findings/lessons happens
    at close_session, so a watcher that skips the close leaves the memory full of raw turns and empty of
    conclusions — the exact state this whole path exists to fix."""
    from engram.connectors import watch
    from engram.connectors.base import ImportMessage, ImportSession

    sessions = [
        ImportSession(session_id="claude-code:a", messages=[ImportMessage(content="a long enough turn")]),
        ImportSession(session_id="codex:b", messages=[ImportMessage(content="another long enough turn")]),
    ]
    monkeypatch.setattr(watch, "load_sessions", lambda paths, **kw: sessions)
    calls = []

    def fake_post(base, key, path, body, timeout):
        calls.append((path, body))
        if path == "/v1/import":
            return {"ok": True, "sessions": 2, "episodes": 2}
        return {"ok": True, "outcomes": 3}

    monkeypatch.setattr(watch, "_post", fake_post)
    result = watch.ingest("http://x", "k", ["p1", "p2"])

    assert [c[0] for c in calls] == ["/v1/import", "/v1/sessions/close", "/v1/sessions/close"]
    assert {c[1]["session_id"] for c in calls[1:]} == {"claude-code:a", "codex:b"}
    assert all(c[1]["outcomes"] is True for c in calls[1:])
    assert result["outcomes"] == 6  # 3 per session


def test_one_failed_distil_does_not_cost_the_others(monkeypatch):
    """The transcripts are already stored by then; a failed distillation is retryable, not fatal."""
    from engram.connectors import watch
    from engram.connectors.base import ImportMessage, ImportSession

    monkeypatch.setattr(watch, "load_sessions", lambda paths, **kw: [
        ImportSession(session_id=f"s{i}", messages=[ImportMessage(content="a long enough turn")])
        for i in range(3)
    ])

    def flaky(base, key, path, body, timeout):
        if path == "/v1/import":
            return {"ok": True, "sessions": 3}
        if body["session_id"] == "s1":
            raise RuntimeError("model timeout")
        return {"ok": True, "outcomes": 2}

    monkeypatch.setattr(watch, "_post", flaky)
    result = watch.ingest("http://x", "k", ["p"])
    assert result["outcomes"] == 4 and result["distil_failed"] == 1


# ---------------------------------------------------------------------------------------------------
# The gate: agent sessions are stored for close-time distillation, never handed to RuleExtractor.
# Measured before the gate: 12 real turns -> 11 junk facts (`The | occupation | nearly empty`), and the
# owner's store held 84 `occupation` rows out of 88 facts, all from the no-LLM fallback period.
# ---------------------------------------------------------------------------------------------------

# Prose shaped like a real working-session transcript: sentence subjects that are pronouns/articles,
# markdown, a config path. RuleExtractor turns this into junk (see test_records_import_still_rule_extracts,
# which proves it DOES extract from the very same text when the source is not an agent session).
_TRANSCRIPT_A = [
    {"role": "user", "content": "The config file is nearly empty. This is now clear: **EKOS is deployed on "
                                "an Ubuntu server** and the backend framework is FastAPI."},
    {"role": "assistant", "content": "This confirms the deployment. The next step is to check nginx."},
]
_TRANSCRIPT_B = [
    {"role": "user", "content": "That module is broken. The extractor is volcano and the judge is deepseek."},
    {"role": "assistant", "content": "It looks like the retry loop is the problem here."},
]


def _agent_sessions(prefix: str = "claude-code") -> list[dict]:
    return [
        {"session_id": f"{prefix}:a", "event_time": 1_756_000_000.0,
         "metadata": {"source": "agent_session"}, "messages": _TRANSCRIPT_A},
        {"session_id": f"{prefix}:b", "event_time": 1_756_000_100.0,
         "metadata": {"source": "agent_session"}, "messages": _TRANSCRIPT_B},
    ]


def test_agent_session_import_without_llm_defers_per_turn_extraction(tmp_path):
    from engram.memory import Memory
    from engram.service import MemoryService

    mem = Memory()
    stats = mem.import_messages(_agent_sessions(), user_id="u")
    assert stats["episodes"] == 2
    assert stats["facts_added"] == 0, stats
    assert stats["facts_deferred"] == 2 and stats["deferred_reason"] == "outcomes_only", stats
    eps = [ep for ep in mem.episodes_doc.values() if ep.user_id == "u"]
    assert len(eps) == 2
    for ep in eps:
        assert ep.consolidated is True  # nothing downstream may ever drain it into RuleExtractor
        assert ep.metadata["extraction"] == "outcomes_only"
        assert ep.metadata["source"] == "agent_session"
    assert stats["summaries"] >= 1  # chunk/summary retrieval of the transcript still works offline

    # close_session is the drain that would otherwise per-turn-extract "pending" episodes.
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="")
    assert svc.import_("u", sessions=_agent_sessions())["facts_deferred"] == 2
    closed = svc.close_session("u", "claude-code:a")
    assert closed["episodes"] == 1
    assert closed["pending_consolidated"] == 0 and closed["facts_added"] == 0, closed


def test_agent_session_import_with_llm_and_consolidate_true_extracts():
    """With an LLM the same 12 turns gave 10 clean facts (`EKOS | deployed_on | Ubuntu server`), so an
    explicit opt-in must still run per-turn extraction."""
    from engram.memory import Memory

    class StubLLM:
        def complete(self, prompt: str, system: str = "", **kw) -> str:
            return '[{"subject": "EKOS", "predicate": "deployed_on", "object": "Ubuntu server"}]'

    mem = Memory(llm=StubLLM())
    stats = mem.import_messages(_agent_sessions(), user_id="u", consolidate=True)
    assert stats["facts_added"] >= 1, stats
    assert stats["facts_deferred"] == 0 and stats["deferred_reason"] is None, stats
    assert all("extraction" not in ep.metadata for ep in mem.episodes_doc.values())


def test_agent_session_consolidate_true_without_llm_reports_no_llm():
    """Asking for facts with no LLM attached is the corrupting case; the answer is a reason, not junk."""
    from engram.memory import Memory

    mem = Memory()
    stats = mem.import_messages(_agent_sessions(), user_id="u", consolidate=True)
    assert stats["facts_added"] == 0, stats
    assert stats["facts_deferred"] == 2 and stats["deferred_reason"] == "no_llm", stats
    assert not [f for f in mem.fact_store.values() if f.user_id == "u"]


def test_records_import_still_rule_extracts():
    """Regression guard for the zero-setup demo: the gate keys on the source, not on the prose. The same
    sentences arriving as `records` go through RuleExtractor exactly as before."""
    from engram.connectors import parse
    from engram.memory import Memory

    records = [{"content": m["content"], "speaker": m["role"], "session_id": "r1"}
               for m in _TRANSCRIPT_A + _TRANSCRIPT_B]
    records.append({"content": "I moved to Shenzhen last year and I work at Moonshot AI.",
                    "speaker": "user", "session_id": "r1"})
    mem = Memory()
    stats = mem.import_messages(parse(records, format="records"), user_id="u")
    assert stats["facts_added"] >= 1, stats
    assert stats["facts_deferred"] == 0 and stats["deferred_reason"] is None


# ---------------------------------------------------------------------------------------------------
# Watcher wire format + state discipline.
# ---------------------------------------------------------------------------------------------------

def _session(sid: str, when: float | None = 1_756_000_000.0):
    from engram.connectors.base import ImportMessage, ImportSession
    return ImportSession(session_id=sid, event_time=when,
                         messages=[ImportMessage(content="a long enough turn to survive")],
                         metadata={"source": "agent_session"})


def _args(tmp_path, **over):
    import argparse
    base = dict(url="http://127.0.0.1:9", key="k", key_file="", since="", limit=25, quiet_seconds=0,
                state=str(tmp_path / "state" / "watch_state.json"), no_outcomes=False,
                extract_facts=False, dry_run=False, once=True, every="")
    base.update(over)
    return argparse.Namespace(**base)


def _quiet_file(tmp_path, name: str = "s.jsonl", age: int = 3600) -> str:
    f = tmp_path / name
    f.write_text("x" * 4096)
    old = f.stat().st_mtime - age
    os.utime(f, (old, old))
    return str(f)


def test_watcher_payload_carries_source_and_session_time(monkeypatch):
    """`metadata.source` is what routes the transcript away from per-turn extraction on the server;
    `event_time` keeps the bi-temporal axis; no `consolidate` key means the server decides (outcomes
    only). Per-message timestamps and file paths never leave the machine."""
    from engram.connectors import watch

    monkeypatch.setattr(watch, "load_sessions", lambda paths, **kw: [_session("claude-code:a")])
    bodies = []

    def fake_post(base, key, path, body, timeout):
        bodies.append((path, body))
        return {"ok": True, "sessions": 1, "episodes": 1, "outcomes": 1}

    monkeypatch.setattr(watch, "_post", fake_post)
    result = watch.ingest("http://x", "k", ["p1"])
    path, body = bodies[0]
    assert path == "/v1/import"
    row = body["sessions"][0]
    assert row["metadata"] == {"source": "agent_session"}
    assert row["event_time"] == 1_756_000_000.0
    assert set(row["messages"][0]) == {"role", "content"}
    assert "consolidate" not in body
    assert result["closed_ok"] == ["claude-code:a"] and result["close_failed"] == []
    assert result["sessions_by_path"] == {"p1": ["claude-code:a"]}

    bodies.clear()
    watch.ingest("http://x", "k", ["p1"], extract_facts=True)
    assert bodies[0][1]["consolidate"] is True

    monkeypatch.setattr(watch, "load_sessions", lambda paths, **kw: [_session("codex:b", when=None)])
    bodies.clear()
    watch.ingest("http://x", "k", ["p2"])
    assert "event_time" not in bodies[0][1]["sessions"][0]


def test_watcher_does_not_strand_sessions_older_than_last_run(tmp_path, monkeypatch):
    """Deriving `since` from last_run silently dropped every transcript a --limit'ed tick did not reach.
    The size ledger is the idempotency mechanism; `since` is only ever the explicit window."""
    from engram.connectors import watch

    old = _quiet_file(tmp_path, age=3 * 86400)
    mtime = os.path.getmtime(old)
    monkeypatch.setattr(watch, "find_sessions",
                        lambda since=None, **kw: [old] if since is None or mtime >= since else [])
    monkeypatch.setattr(watch, "load_sessions", lambda paths, **kw: [_session("s")])
    monkeypatch.setattr(watch, "_post", lambda *a, **k: {"ok": True, "episodes": 1, "outcomes": 0})

    state_path = tmp_path / "state" / "watch_state.json"
    state_path.parent.mkdir()
    state_path.write_text(json.dumps({"seen": {}, "last_run": mtime + 86400}))
    out = watch.run_once(_args(tmp_path))
    assert out["exit"] == 0 and out["sessions"] == 1, out
    assert old in json.loads(state_path.read_text())["seen"]


def test_failed_close_is_not_marked_seen_until_third_failure(tmp_path, monkeypatch):
    """A stored-but-not-distilled transcript must be retried, or the memory keeps raw turns and no
    conclusions — bounded, so one undigestible file cannot hold a slot of every tick's --limit."""
    from engram.connectors import watch

    path = _quiet_file(tmp_path)
    monkeypatch.setattr(watch, "find_sessions", lambda **kw: [path])
    monkeypatch.setattr(watch, "load_sessions", lambda paths, **kw: [_session("s")])

    def flaky(base, key, route, body, timeout):
        if route == "/v1/import":
            return {"ok": True, "episodes": 1}
        raise RuntimeError("model timeout")

    monkeypatch.setattr(watch, "_post", flaky)
    args = _args(tmp_path)
    for n in (1, 2):
        out = watch.run_once(args)
        assert out["exit"] == 0 and out["close_failed"] == 1
        state = watch._load_state(args.state)
        assert path not in state["seen"], state
        assert state["close_failures"][path] == n
        assert state["last_result"]["close_failed"] == 1
    watch.run_once(args)
    state = watch._load_state(args.state)
    assert path in state["seen"] and path not in state["close_failures"], state


def test_unreachable_server_exits_75_and_leaves_state_untouched(tmp_path, monkeypatch):
    import urllib.error

    from engram.connectors import watch

    path = _quiet_file(tmp_path)
    monkeypatch.setattr(watch, "find_sessions", lambda **kw: [path])
    monkeypatch.setattr(watch, "load_sessions", lambda paths, **kw: [_session("s")])

    def down(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(watch, "_post", down)
    args = _args(tmp_path)
    assert watch.run_once(args)["exit"] == 75
    assert not os.path.exists(args.state)


def test_every_loop_runs_n_times_with_injected_sleep(tmp_path, monkeypatch):
    from engram.connectors import watch

    runs = []
    monkeypatch.setattr(watch, "run_once", lambda args: runs.append(args.state) or {"exit": 0})
    naps = []

    def fake_sleep(seconds):
        naps.append(seconds)
        if len(naps) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(watch, "_sleep", fake_sleep)
    code = watch.main(["--every", "2m", "--key", "k", "--state", str(tmp_path / "s.json")])
    assert code == 0 and len(runs) == 3 and naps == [120, 120, 120]


def test_lock_makes_second_run_skip(tmp_path, monkeypatch):
    """launchd fires every 30 minutes regardless of whether the last backfill tick is still posting."""
    import fcntl

    from engram.connectors import watch

    path = _quiet_file(tmp_path)
    monkeypatch.setattr(watch, "find_sessions", lambda **kw: [path])
    monkeypatch.setattr(watch, "load_sessions", lambda paths, **kw: [_session("s")])
    posted = []
    monkeypatch.setattr(watch, "_post", lambda *a, **k: posted.append(a) or {"ok": True})

    args = _args(tmp_path)
    os.makedirs(os.path.dirname(args.state))
    holder = open(os.path.join(os.path.dirname(args.state), "watch.lock"), "a+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        out = watch.run_once(args)
        assert out == {"exit": 0, "skipped": "locked"} and posted == []
    finally:
        holder.close()
    assert watch.run_once(args)["exit"] == 0 and posted  # released -> the next tick proceeds


def test_key_file_and_url_env_alias(tmp_path, monkeypatch):
    from engram.connectors import watch

    kf = tmp_path / "watch.key"
    kf.write_text("from-file\n")
    assert watch._resolve_key(_args(tmp_path, key="", key_file=str(kf))) == "from-file"
    assert watch._resolve_key(_args(tmp_path, key="explicit", key_file=str(kf))) == "explicit"
    monkeypatch.setenv("ENGRAM_API_KEY", "from-env")
    assert watch._resolve_key(_args(tmp_path, key="", key_file="")) == "from-env"
    assert watch._parse_duration("30m") == 1800 and watch._parse_duration("2h") == 7200
    assert watch._parse_duration("1d") == 86400


def test_a_tick_larger_than_one_request_is_split_not_refused():
    """25 real transcripts serialize to ~18 MB against a 2 MiB server cap. Sending them as one body was
    refused with 413, and because the server closes the connection mid-body urllib raised "Broken pipe",
    which the caller read as "server unreachable" and retried forever — 115 identical failures over two
    days, nothing ingested."""
    from engram.connectors import watch
    rows = [{"session_id": f"s{i}", "messages": [{"role": "user", "content": "x" * 400_000}]}
            for i in range(6)]
    batches, oversized = watch.chunk_rows(rows, max_bytes=1_000_000)

    assert not oversized
    assert len(batches) > 1 and sum(len(b) for b in batches) == 6   # split, and nothing dropped
    assert all(sum(watch._row_bytes(r) for r in b) <= 1_000_000 for b in batches)


def test_a_session_too_large_to_send_is_named_not_retried_forever():
    """A session is the atom — splitting one would hand the extractor half a conversation — so an
    oversized transcript is reported instead of silently dropped or endlessly retried."""
    from engram.connectors import watch
    rows = [{"session_id": "small", "messages": [{"role": "user", "content": "hi there"}]},
            {"session_id": "huge", "messages": [{"role": "user", "content": "x" * 3_000_000}]}]
    batches, oversized = watch.chunk_rows(rows, max_bytes=1_000_000)

    assert [r["session_id"] for r in oversized] == ["huge"]
    assert [r["session_id"] for b in batches for r in b] == ["small"]
