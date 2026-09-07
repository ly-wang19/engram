"""Tests for eval/facility.py — the L0-L4 facility harness.

SYNTHETIC FIXTURES ONLY. The real corpus this harness reads (the owner's Claude Code and Codex
transcripts) contains colleague names and business detail, so no test may touch it: each test builds a
tiny fake $HOME and points every path at it. Each metric has a test that fails if the metric is computed
the plausible-but-wrong way — the tool-name substring match, the missing `"tool_result"` prefilter, the
unterminated watch.log block, the ledger treated as evidence of a close.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from argparse import Namespace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import facility  # noqa: E402

PROJECT_DIR = "-Users-nobody-SECRETPROJ"
CLAUDE_UUID = "aaaaaaaa-1111-2222-3333-444444444444"
SESSION_LABEL = "claude-code:SECRETPROJ:aaaaaaaa"
SECRET_TEXT = "SECRETFACTTEXT the owner told the agent about a colleague"
PAD = "x" * 2600  # push the file past find_sessions' 2048-byte floor


def _cc_row(**kw) -> str:
    row = {"type": "assistant", "timestamp": "2026-09-01T10:00:00.000Z",
           "isSidechain": False, "cwd": "/nowhere"}
    row.update(kw)
    return json.dumps(row)


def _write(path: str, lines: list[str], mtime_age_s: float = 7200.0) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    old = time.time() - mtime_age_s
    os.utime(path, (old, old))
    return path


def claude_transcript(home: str, name: str = f"{CLAUDE_UUID}.jsonl",
                      project: str = PROJECT_DIR, recall_name: str = "mcp__engram__engram_recall",
                      with_result: bool = True, mtime_age_s: float = 7200.0) -> str:
    """One Claude Code transcript: a real engram_recall tool_use, its tool_result, prose mentions, pad.

    The prose block names the same tool three times. A substring matcher over the raw bytes counts 4
    calls here; the structural matcher must count 1.
    """
    lines = [
        _cc_row(message={"role": "user", "content": [{"type": "text", "text": PAD}]}),
        _cc_row(message={"role": "assistant", "content": [
            {"type": "text",
             "text": f"I will call {recall_name} now. Note that {recall_name} is the read tool; "
                     f"{recall_name} takes a query."},
        ]}),
        _cc_row(message={"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_01", "name": recall_name, "input": {"query": "q"}},
        ]}),
        _cc_row(message={"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_02", "name": "Read", "input": {"file_path": "/tmp/a"}},
        ]}),
    ]
    if with_result:
        # This row's only prefilter-visible tokens are "tool_result" / "tool_use_id". Neither contains
        # the bytes `"tool_use"` (the closing quote is missing in "tool_use_id"), so a prefilter that
        # omits `"tool_result"` drops this line and reach silently reads 0.
        result = _cc_row(type="user", message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_01",
             "content": f"Relevant memory (~120 tokens):\n\n{SECRET_TEXT}"},
        ]})
        assert b'"tool_use"' not in result.encode()  # the whole point of the separate needle
        lines.append(result)
    return _write(os.path.join(home, ".claude", "projects", project, name), lines, mtime_age_s)


def codex_transcript(home: str, name: str = "rollout-2026-09-01T10-00-00-01a05881-aaaa.jsonl",
                     mtime_age_s: float = 7200.0) -> str:
    """One Codex transcript: a real (non-engram) function_call plus engram_* named only in prose."""
    lines = [
        json.dumps({"type": "response_item", "timestamp": "2026-09-01T10:00:00.000Z",
                    "payload": {"type": "message", "role": "user",
                                "content": [{"type": "input_text", "text": PAD}]}}),
        json.dumps({"type": "response_item", "timestamp": "2026-09-01T10:00:01.000Z",
                    "payload": {"type": "message", "role": "assistant",
                                "content": [{"type": "output_text",
                                             "text": "mcp__engram__engram_recall would help here"}]}}),
        json.dumps({"type": "response_item", "timestamp": "2026-09-01T10:00:02.000Z",
                    "payload": {"type": "function_call", "name": "exec_command",
                                "call_id": "call_1", "arguments": "{}"}}),
    ]
    return _write(os.path.join(home, ".codex", "sessions", "2026", "09", "01", name),
                  lines, mtime_age_s)


def _stamp(epoch: float) -> str:
    """watch.py's own timestamp format: local time WITH the offset (connectors/watch.py::_ts)."""
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def _files(paths: list[str]) -> list[tuple[str, os.stat_result]]:
    return [(p, os.stat(p)) for p in paths]


# ---------------------------------------------------------------------------
# 1. the matcher: structural calls only, mcp__ prefix stripped
# ---------------------------------------------------------------------------


def test_matcher_counts_structural_calls_not_prose(tmp_path):
    home = str(tmp_path)
    cc = claude_transcript(home)
    cx = codex_transcript(home)

    raw = open(cc, "rb").read() + open(cx, "rb").read()
    assert raw.count(b"engram_recall") >= 4, "fixture must inflate a substring matcher"

    scan = facility.scan_transcripts(_files([cc, cx]), excludes=[])
    assert scan.calls["recall"] == 1          # not 4, and not 0 despite the mcp__engram__ prefix
    assert sum(scan.calls.values()) == 1
    assert scan.control["Read"] == 1 and scan.control["exec_command"] == 1
    assert scan.prose_mentions >= 2           # the tripwire's numerator still sees the prose


def test_strip_mcp_prefix_is_structural_not_substring():
    assert facility.strip_mcp_prefix("mcp__engram__engram_recall") == "engram_recall"
    assert facility.strip_mcp_prefix("mcp__my_server__engram_stats") == "engram_stats"
    assert facility.strip_mcp_prefix("engram_remember") == "engram_remember"
    assert facility.is_engram_tool("mcp__engram__engram_recall")
    assert not facility.is_engram_tool("Bash")


def test_self_development_calls_are_excluded_and_counted(tmp_path):
    home = str(tmp_path)
    cc = claude_transcript(home, project="-Users-nobody-super-memory")
    scan = facility.scan_transcripts(_files([cc]), excludes=["super-memory"])
    assert scan.calls == {} and scan.excluded_selfdev_calls == 1


# ---------------------------------------------------------------------------
# 2. reach: the tool_result join depends on its own prefilter needle
# ---------------------------------------------------------------------------


def test_recall_reach_joins_tool_results(tmp_path):
    home = str(tmp_path)
    cc = claude_transcript(home)
    scan = facility.scan_transcripts(_files([cc]), excludes=[])
    assert scan.recall_calls == 1
    assert scan.recall_non_empty == 1


def test_recall_reach_is_zero_without_the_tool_result_needle(tmp_path, monkeypatch):
    """Drop `"tool_result"` from the prefilter and reach collapses — the reason the set is fixed."""
    home = str(tmp_path)
    cc = claude_transcript(home)
    # Only the needle set is patched. Stubbing `_tool_results` as well would make this test pass
    # against a build where the needle is inert, which is exactly the failure it exists to catch.
    monkeypatch.setattr(facility, "_CALL_NEEDLES",
                        tuple(n for n in facility._CALL_NEEDLES if n != b'"tool_result"'))
    scan = facility.scan_transcripts(_files([cc]), excludes=[])
    assert scan.recall_calls == 1 and scan.recall_non_empty == 0


def codex_engram_transcript(home: str, name: str = "rollout-2026-09-01T11-00-00-01a05881-bbbb.jsonl",
                            call_id: str = "call_engram_1", empty: bool = False,
                            mtime_age_s: float = 7200.0) -> str:
    """A Codex transcript where engram_recall is called AND answered.

    The answer comes back as its own `function_call_output` payload row wrapped in Codex's
    "Wall time: N seconds\nOutput:\n" preamble — a shape the Claude `tool_result` join cannot see.
    """
    body = (facility._EMPTY_RECALL if empty
            else "Relevant memory (~200 tokens):\n\n" + SECRET_TEXT)
    lines = [
        json.dumps({"type": "response_item", "timestamp": "2026-09-01T11:00:00.000Z",
                    "payload": {"type": "message", "role": "user",
                                "content": [{"type": "input_text", "text": PAD}]}}),
        json.dumps({"type": "response_item", "timestamp": "2026-09-01T11:00:01.000Z",
                    "payload": {"type": "function_call", "name": "mcp__engram__engram_recall",
                                "call_id": call_id, "arguments": "{}"}}),
        json.dumps({"type": "response_item", "timestamp": "2026-09-01T11:00:02.000Z",
                    "payload": {"type": "function_call_output", "call_id": call_id,
                                "output": "Wall time: 0.2139 seconds\nOutput:\n" + body}}),
    ]
    out = _write(os.path.join(home, ".codex", "sessions", "2026", "09", "01", name),
                 lines, mtime_age_s)
    raw = open(out, "rb").read()
    # The whole reason this shape needs its own needle: none of the call needles appear in it.
    assert b'"function_call_output"' in raw and b'"tool_result"' not in raw
    return out


def test_codex_recall_is_joined_to_its_own_result_row(tmp_path):
    """A Codex recall must be able to REACH, not just land in the denominator.

    Without the `function_call_output` join a Codex recall is counted as a call and can never be
    counted as answered, so reach reads low in exact proportion to how much Codex uses the memory.
    On the owner's corpus that printed 13/50 = 26% where the truth is 50/50 = 100%.
    """
    home = str(tmp_path)
    cx = codex_engram_transcript(home)
    scan = facility.scan_transcripts(_files([cx]), excludes=[])
    assert scan.calls["recall"] == 1
    assert (scan.recall_calls, scan.recall_non_empty) == (1, 1)


def test_codex_empty_recall_is_not_reached_through_the_wall_time_preamble(tmp_path):
    """The preamble must be stripped, or every Codex recall passes on preamble length alone."""
    home = str(tmp_path)
    cx = codex_engram_transcript(home, empty=True)
    scan = facility.scan_transcripts(_files([cx]), excludes=[])
    assert (scan.recall_calls, scan.recall_non_empty) == (1, 0)


def test_a_resumed_transcript_replays_calls_and_must_not_recount_them(tmp_path):
    """Resuming a session writes a new file replaying the old tool_use blocks, ids and all.

    Counting the replay reports reads that never happened. Measured on the owner's corpus: 182 raw
    engram calls, only 110 distinct — a 65% overstatement of L2, the rung being grown.
    """
    home = str(tmp_path)
    first = claude_transcript(home)
    resumed = claude_transcript(home, name="bbbbbbbb-1111-2222-3333-444444444444.jsonl")
    assert b"toolu_01" in open(resumed, "rb").read()   # same call id: it is the same call replayed

    scan = facility.scan_transcripts(_files([first, resumed]), excludes=[])
    assert scan.calls["recall"] == 1, "the replay is not a second read"
    assert scan.replayed_calls == 1
    assert (scan.recall_calls, scan.recall_non_empty) == (1, 1)

    # Two genuinely different calls still count twice — the guard is on the id, not on the file.
    other = claude_transcript(home, name="cccccccc-1111-2222-3333-444444444444.jsonl")
    with open(other, "r+", encoding="utf-8") as fh:
        body = fh.read().replace("toolu_01", "toolu_99")
        fh.seek(0), fh.write(body), fh.truncate()
    scan2 = facility.scan_transcripts(_files([first, other]), excludes=[])
    assert scan2.calls["recall"] == 2 and scan2.replayed_calls == 0


def test_empty_recall_result_does_not_count_as_reached(tmp_path):
    home = str(tmp_path)
    path = os.path.join(home, ".claude", "projects", PROJECT_DIR, CLAUDE_UUID + ".jsonl")
    _write(path, [
        _cc_row(message={"role": "user", "content": [{"type": "text", "text": PAD}]}),
        _cc_row(message={"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_01", "name": "mcp__engram__engram_recall",
             "input": {"query": "q"}}]}),
        _cc_row(type="user", message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_01",
             "content": "No relevant memory found for that query."}]}),
    ])
    scan = facility.scan_transcripts(_files([path]), excludes=[])
    assert scan.recall_calls == 1 and scan.recall_non_empty == 0


# ---------------------------------------------------------------------------
# 3-4. lag from watch.log, and the ledger that is not evidence
# ---------------------------------------------------------------------------


def _watch_log(tmp_path, fed_iso: str, key: str) -> str:
    path = str(tmp_path / "watch.log")
    with open(path, "w", encoding="utf-8") as fh:
        # a --dry-run block: paths listed, never fed
        fh.write("2 session(s) to ingest (2 new; backlog 9):\n")
        fh.write(f"  {key}\n")
        fh.write("\n[dry-run] would send 2 session(s), 40 turn(s) — nothing was stored\n")
        # a block the server refused
        fh.write("1 session(s) to ingest (1 new; backlog 9):\n")
        fh.write(f"  {key}\n")
        fh.write("2026-09-01T09:00:00+08:00 server unreachable (http://x): nothing marked seen\n")
        # the only block that actually stored anything
        fh.write("1 session(s) to ingest (1 new; backlog 8):\n")
        fh.write(f"  {key}\n")
        fh.write(f"{fed_iso} fed 1 session(s) (1 new) · 1 episode(s) · 2 conclusion(s) · "
                 "0 close failure(s) · 3.0s\n")
    return path


def test_lag_only_counts_fed_terminated_blocks(tmp_path):
    home = str(tmp_path / "home")
    cc = claude_transcript(home, mtime_age_s=3600.0)
    key = f"{os.path.basename(os.path.dirname(cc))}/{os.path.basename(cc)}"
    index = {(os.path.basename(os.path.dirname(cc)), os.path.basename(cc)): cc}
    state = {"seen": {cc: os.stat(cc).st_size}}
    fed_at = os.stat(cc).st_mtime + 600
    log = _watch_log(tmp_path, _stamp(fed_at), key)

    samples = facility.parse_watch_lag(log, state, index, time.time())
    assert len(samples) == 1                      # not 3: dry-run and unreachable blocks are not feeds
    assert 595 <= samples[0] <= 605


def test_lag_skips_a_transcript_that_grew_since_it_was_fed(tmp_path):
    home = str(tmp_path / "home")
    cc = claude_transcript(home, mtime_age_s=3600.0)
    key = f"{os.path.basename(os.path.dirname(cc))}/{os.path.basename(cc)}"
    index = {(os.path.basename(os.path.dirname(cc)), os.path.basename(cc)): cc}
    fed_at = os.stat(cc).st_mtime + 600
    log = _watch_log(tmp_path, _stamp(fed_at), key)

    stale = {"seen": {cc: os.stat(cc).st_size - 10}}  # the file grew: today's mtime is not ingest-time
    assert facility.parse_watch_lag(log, stale, index, time.time()) == []


def test_hook_lag_is_read_from_the_hook_log(tmp_path):
    log = str(tmp_path / "hook.log")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "fed", "lag_s": 12.5}) + "\n")
        fh.write("2026-09-07T10:00:00+08:00 fed 1 session lag_s=31 outcomes=2\n")
        fh.write("2026-09-07T10:01:00+08:00 refused: target mismatch\n")
    assert facility.parse_hook_lag(log) == [12.5, 31.0]
    assert facility.parse_hook_lag(str(tmp_path / "missing.log")) == []


# ---------------------------------------------------------------------------
# stub server + full-report fixtures
# ---------------------------------------------------------------------------


class _Stub(BaseHTTPRequestHandler):
    sessions: list[dict] = []

    def do_GET(self):  # noqa: N802
        path = self.path
        if path.startswith("/v1/stats"):
            body = {"counts": {"episodes": 3}}
        elif path.startswith("/v1/sessions"):
            body = {"sessions": self.sessions,
                    "page": {"total": len(self.sessions), "has_more": False, "next_offset": None}}
        elif "kind=outcomes&status=superseded" in path:
            body = {"counts": {}, "facts_page": {"total": 0}}
        elif "kind=attributes" in path:
            body = {"counts": {}, "facts_page": {"total": 0}}
        elif "kind=outcomes&status=live" in path:
            body = {"counts": {}, "facts_page": {"total": 5}}
        else:
            body = {"counts": {"facts_outcomes": 5}, "facts_page": {"total": 5}}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_a):
        pass


@pytest.fixture()
def stub_server():
    _Stub.sessions = [{"id": SESSION_LABEL, "facts_added": 3}]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _args(home: str, url: str, **over) -> Namespace:
    mcp = os.path.join(home, "claude.json")
    with open(mcp, "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": {"engram": {"env": {"ENGRAM_API_URL": "http://elsewhere:8456",
                                                     "ENGRAM_API_KEY": "other"}}}}, fh)
    args = Namespace(
        url=url, key="k", key_file="", days=7, all=False,
        roots=f"{home}/.claude/projects,{home}/.codex/sessions",
        state=os.path.join(home, "watch_state.json"),
        watch_log=os.path.join(home, "watch.log"),
        hook_log=os.path.join(home, "hook.log"),
        quiet_seconds=900, tick_interval="30m",
        exclude_project_substr=["super-memory"], mcp_config=mcp,
        json=False, out="", no_out=True, assert_no_regress=False,
    )
    for k, v in over.items():
        setattr(args, k, v)
    return args


# ---------------------------------------------------------------------------
# 4b. a `seen` entry written after MAX_CLOSE_FAILURES is ledger-done, NOT L1-closed
# ---------------------------------------------------------------------------


def test_gave_up_session_is_ledger_done_but_not_closed(tmp_path, stub_server, monkeypatch):
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    closed = claude_transcript(home)
    # A second transcript the watcher gave up on: it is in `seen` (watch.py marks it after 3 close
    # failures so it stops occupying a slot every tick) but the server never got a session for it.
    gave_up = claude_transcript(home, name="bbbbbbbb-9999-0000-1111-222222222222.jsonl")
    with open(os.path.join(home, "watch_state.json"), "w", encoding="utf-8") as fh:
        json.dump({"seen": {closed: os.stat(closed).st_size,
                            gave_up: os.stat(gave_up).st_size}}, fh)

    report = facility.build_report(_args(home, stub_server))
    assert report["l1"]["finished"] == 2
    assert report["l1"]["closed"] == 1            # the give-up is NOT evidence of a close
    assert report["l1"]["ledger_done"] == 2
    assert report["l1"]["ledger_disagree"] == 1
    assert report["l1"]["yield"] == 1.0           # the one closed session had facts_added > 0


# ---------------------------------------------------------------------------
# 5. golden output: counts and rates only, never content
# ---------------------------------------------------------------------------


def test_report_never_prints_a_path_a_fact_or_a_session_id(tmp_path, stub_server, monkeypatch):
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    claude_transcript(home)
    codex_transcript(home)
    report = facility.build_report(_args(home, stub_server))
    text = facility.render(report) + "\n" + json.dumps(report, ensure_ascii=False)

    for forbidden in ("SECRETPROJ", "SECRETFACTTEXT", SESSION_LABEL, CLAUDE_UUID,
                      "rollout-", home, ".jsonl"):
        assert forbidden not in text, f"{forbidden!r} leaked into the report"
    assert "L0 exists" in text and "L4 depended on" in text
    assert "<-- DIFFERENT MEMORY" in text        # the write/read split is above the rungs, not a footnote
    assert report["l3"]["useful_rate"] is None
    assert report["rungs"]["L4"] == "not_measurable"


def test_rungs_read_off_the_measured_numbers(tmp_path, stub_server, monkeypatch):
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    claude_transcript(home)
    report = facility.build_report(_args(home, stub_server))
    assert report["rungs"]["L0"] == "met"                  # conclusions + episodes exist
    assert report["rungs"]["L1"] == "not_met"              # coverage is fine, lag has no samples
    assert report["rungs"]["L2"] == "not_met"              # the recall is in a past ISO week
    assert report["rungs"]["L3"] == "not_met"              # supersession rate is 0
    assert report["l3"]["supersession_rate"] == 0.0
    # The current ISO week is always in the series, so a stale spike cannot read as a healthy trend.
    assert facility._iso_week(time.time()) in report["l2"]["weeks"]


# ---------------------------------------------------------------------------
# 6. exit codes: 2 = instrument broken, 1 = facility regressed. Never conflated.
# ---------------------------------------------------------------------------


def test_zero_control_calls_is_instrument_broken_not_a_result(tmp_path, stub_server, monkeypatch):
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    claude_transcript(home)
    report = facility.build_report(_args(home, stub_server))
    assert report["l2"]["control_total"] > 0
    code, reasons = facility.check_regression(report, previous=None)
    assert code == facility.EXIT_OK

    report["l2"]["control_total"] = 0
    code, reasons = facility.check_regression(report, previous=None)
    assert code == facility.EXIT_INSTRUMENT_BROKEN
    assert any("control" in r for r in reasons)


def test_unreachable_server_is_instrument_broken(tmp_path, monkeypatch):
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    claude_transcript(home)
    report = facility.build_report(_args(home, "http://127.0.0.1:1"))
    code, reasons = facility.check_regression(report, previous=None)
    assert code == facility.EXIT_INSTRUMENT_BROKEN
    assert any("unreachable" in r for r in reasons)


def test_coverage_drop_over_five_points_is_a_regression(tmp_path, stub_server, monkeypatch):
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    claude_transcript(home)
    claude_transcript(home, name="bbbbbbbb-9999-0000-1111-222222222222.jsonl")
    report = facility.build_report(_args(home, stub_server))
    assert report["l1"]["coverage"] == 0.5

    code, reasons = facility.check_regression(report, {"l1": {"coverage": 0.54}, "rungs": {}})
    assert code == facility.EXIT_OK                       # 4 pp is inside the band

    code, reasons = facility.check_regression(report, {"l1": {"coverage": 0.9}, "rungs": {}})
    assert code == facility.EXIT_REGRESSED
    assert any("coverage" in r for r in reasons)

    code, reasons = facility.check_regression(report, {"rungs": {"L1": "met"}})
    assert code == facility.EXIT_REGRESSED and any("L1 dropped" in r for r in reasons)


def test_lag_doubling_and_recall_falling_to_zero_are_regressions(tmp_path, stub_server, monkeypatch):
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    claude_transcript(home)
    report = facility.build_report(_args(home, stub_server))
    report["l1"]["lag"]["p50_s"] = 400.0
    code, reasons = facility.check_regression(report, {"l1": {"lag": {"p50_s": 100.0}}, "rungs": {}})
    assert code == facility.EXIT_REGRESSED and any("lag p50" in r for r in reasons)

    previous = {"rungs": {}, "l2": {"weeks": {"2026-W20": {"recall": 3}}}}
    report["l1"]["lag"]["p50_s"] = None
    for week in report["l2"]["weeks"].values():
        week["recall"] = 0
    code, reasons = facility.check_regression(report, previous)
    assert code == facility.EXIT_REGRESSED and any("recall fell to 0" in r for r in reasons)


def test_bare_run_exits_zero_even_when_the_instrument_is_broken(tmp_path, monkeypatch, capsys):
    """`--assert-no-regress` is what turns findings into exit codes; a bare run always reports."""
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    claude_transcript(home)
    mcp = os.path.join(home, "claude.json")
    with open(mcp, "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": {}}, fh)
    argv = ["--url", "http://127.0.0.1:1", "--key", "k", "--no-out", "--mcp-config", mcp,
            "--roots", f"{home}/.claude/projects,{home}/.codex/sessions",
            "--state", os.path.join(home, "watch_state.json"),
            "--watch-log", os.path.join(home, "watch.log"),
            "--hook-log", os.path.join(home, "hook.log")]
    assert facility.main(argv) == facility.EXIT_OK
    assert facility.main(argv + ["--assert-no-regress"]) == facility.EXIT_INSTRUMENT_BROKEN
    assert "INSTRUMENT BROKEN" in capsys.readouterr().out


def test_ledger_appends_one_line_per_run(tmp_path, stub_server, monkeypatch):
    home = str(tmp_path)
    monkeypatch.setenv("HOME", home)
    claude_transcript(home)
    mcp = os.path.join(home, "claude.json")
    with open(mcp, "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": {}}, fh)
    out = os.path.join(home, "facility.jsonl")
    argv = ["--url", stub_server, "--key", "k", "--out", out, "--mcp-config", mcp,
            "--roots", f"{home}/.claude/projects,{home}/.codex/sessions",
            "--state", os.path.join(home, "watch_state.json"),
            "--watch-log", os.path.join(home, "watch.log"),
            "--hook-log", os.path.join(home, "hook.log")]
    facility.main(argv)
    facility.main(argv)
    rows = [json.loads(line) for line in open(out, encoding="utf-8") if line.strip()]
    assert len(rows) == 2 and all("rungs" in r for r in rows)


def test_trend_compares_like_windows_only(tmp_path):
    """A `--days 7` run must not be differenced against an `--all` run: different measurement."""
    out = str(tmp_path / "ledger.jsonl")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"window_days": 7, "l1": {"coverage": 0.77}}) + "\n")
        fh.write(json.dumps({"window_days": None, "l1": {"coverage": 0.23}}) + "\n")
    assert facility._previous_line(out, 7)["l1"]["coverage"] == 0.77
    assert facility._previous_line(out, None)["l1"]["coverage"] == 0.23
    assert facility._previous_line(out, 30) is None
    assert facility._previous_line(str(tmp_path / "nope.jsonl"), 7) is None
