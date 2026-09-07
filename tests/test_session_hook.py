"""The SessionEnd hook: the worker, the settings editor, and the handshake with the watcher.

Two things every test here has to be true about, because getting either wrong is worse than not shipping
the feature:

* **The owner's real ~/.claude/settings.json is never opened for writing.** Every test passes
  `--settings` explicitly at a tmp path, and `test_dry_run_touches_nothing` asserts the real file's
  sha256 is identical before and after a dry run.
* **Nothing here talks to a network, an LLM, or a real transcript.** The transcripts are synthetic, the
  server is a recorder standing in for `/v1/import` + `/v1/sessions/close`, and the settings fixtures are
  hand-built with third-party paths.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import pytest

from engram.connectors import hook_install as hi
from engram.connectors import session_hook, watch
from engram.connectors.agent_sessions import _session_label

REAL_SETTINGS = os.path.expanduser("~/.claude/settings.json")
PY = "/opt/python/bin/python3"


@pytest.fixture(autouse=True)
def _never_really_detach(monkeypatch):
    """os.setsid() in-process would move the whole pytest run into a new session."""
    calls: list[int] = []
    monkeypatch.setattr(session_hook, "_setsid", lambda: calls.append(1))
    return calls


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# --- fixtures ---------------------------------------------------------------------------------------

THIRD_PARTY = ("if [ -f '/home/demo/.thirdparty/hook.sh' ]; then /bin/sh "
               "'/home/demo/.thirdparty/hook.sh'; else { command -p cat 2>/dev/null || cat; } "
               ">/dev/null 2>&1 || :; fi")


def _leaf(command: str = THIRD_PARTY, timeout: int = 10) -> dict:
    return {"type": "command", "command": command, "timeout": timeout}


def _owner_shaped_hooks() -> dict:
    """The shape of the owner's ten real slots: six with no matcher, four with "matcher": "*"."""
    no_matcher = ["UserPromptSubmit", "Stop", "StopFailure", "SubagentStart", "SubagentStop",
                  "TeammateIdle"]
    matched = ["PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"]
    hooks = {slot: [{"hooks": [_leaf()]}] for slot in no_matcher}
    hooks.update({slot: [{"matcher": "*", "hooks": [_leaf()]}] for slot in matched})
    return hooks


SETTINGS_FIXTURES = {
    "empty": {},
    "no_hooks_key": {"model": "claude-opus-5", "theme": "dark-ansi"},
    "owner_shaped": {"cleanupPeriodDays": 36500, "model": "claude-opus-5",
                     "hooks": _owner_shaped_hooks(),
                     "statusLine": {"type": "command", "command": "/bin/sh '/home/demo/status.sh'"},
                     "enabledPlugins": {"swift-lsp@demo": True}},
    "third_party_session_end": {"hooks": {"SessionEnd": [{"hooks": [_leaf("/bin/sh '/home/demo/end.sh'")]}],
                                          "Stop": [{"hooks": [_leaf()]}]}},
}


def _transcript(tmp_path, turns: int = 3, secret: str = "") -> str:
    """A synthetic Claude Code transcript, over find_sessions' 2048-byte floor."""
    project = tmp_path / "roots" / "claude" / "-Users-demo-Projects-widget"
    project.mkdir(parents=True, exist_ok=True)
    path = project / "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0.jsonl"
    filler = "The retrieval weights were the thing we argued about for an hour. " * 8
    rows = []
    for i in range(turns):
        rows.append({"type": "user", "timestamp": f"2026-09-07T08:0{i}:00Z",
                     "message": {"role": "user", "content": [
                         {"type": "text", "text": f"Question {i}: {filler}{secret}"}]}})
        rows.append({"type": "assistant", "timestamp": f"2026-09-07T08:0{i}:30Z",
                     "message": {"role": "assistant", "content": [
                         {"type": "text", "text": f"Answer {i}: {filler}"}]}})
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert path.stat().st_size >= session_hook.MIN_BYTES
    return str(path)


class StubServer:
    """Stands in for the pilot: records what was imported and what was closed, and answers the one
    read the harness's L1 join uses (`GET /v1/sessions`) out of the same recording."""

    def __init__(self, close_fails: bool = False):
        self.imports: list[dict] = []
        self.closed: list[str] = []
        self.close_fails = close_fails

    def post(self, base_url, api_key, path, body, timeout):
        if path == "/v1/import":
            self.imports.append(body)
            return {"ok": True, "sessions": len(body["sessions"]),
                    "episodes": sum(len(s["messages"]) for s in body["sessions"]), "skipped": 0}
        if path == "/v1/sessions/close":
            if self.close_fails:
                raise RuntimeError("the distiller is down")
            self.closed.append(body["session_id"])
            return {"ok": True, "outcomes": 2}
        raise AssertionError(f"unexpected POST {path}")

    def sessions(self) -> dict:
        """What `GET /v1/sessions?limit=...` returns — the evidence store C1 defines "closed" against."""
        return {"sessions": [{"id": sid, "facts_added": 2} for sid in self.closed]}


def _hook_args(tmp_path, url="http://127.0.0.1:8766", target_in_state=None) -> tuple[list[str], str]:
    key_file = tmp_path / "watch.key"
    key_file.write_text("demo-key\n")
    state = tmp_path / "watch_state.json"
    if target_in_state is not None:
        state.write_text(json.dumps({"target": target_in_state, "seen": {}}))
    argv = ["--url", url, "--key-file", str(key_file), "--state", str(state),
            "--claims", str(tmp_path / "hook_claims.jsonl")]
    return argv, str(key_file)


# --- 1. the dry run writes nothing, anywhere ---------------------------------------------------------

@pytest.mark.skipif(not os.path.exists(REAL_SETTINGS), reason="no real settings.json on this machine")
def test_dry_run_touches_neither_the_temp_file_nor_the_owners(tmp_path, capsys):
    settings = tmp_path / "s.json"
    settings.write_text(hi.dumps(SETTINGS_FIXTURES["owner_shaped"], 2))
    key_file = tmp_path / "watch.key"
    key_file.write_text("demo-key\n")
    state = tmp_path / "watch_state.json"
    state.write_text(json.dumps({"target": watch._target("http://127.0.0.1:8766", "demo-key")}))

    before, before_mtime = _sha(str(settings)), settings.stat().st_mtime
    real_before = _sha(REAL_SETTINGS)

    code = watch.main(["--install-hook", "--dry-run", "--settings", str(settings),
                       "--python", PY, "--url", "http://127.0.0.1:8766",
                       "--key-file", str(key_file), "--state", str(state)])
    out = capsys.readouterr().out

    assert code == watch.EXIT_OK
    assert "[dry-run] nothing was written" in out
    assert '+    "SessionEnd": [' in out                    # a real unified diff, not a summary
    assert f"target      : {watch._target('http://127.0.0.1:8766', 'demo-key')}" in out
    assert "matches watch_state.json" in out
    assert _sha(str(settings)) == before and settings.stat().st_mtime == before_mtime
    assert _sha(REAL_SETTINGS) == real_before
    assert not list(tmp_path.glob("*.engram-bak-*"))


def test_dry_run_names_a_target_mismatch_before_it_can_bite(tmp_path, capsys):
    settings = tmp_path / "s.json"
    settings.write_text(hi.dumps({}, 2))
    key_file = tmp_path / "watch.key"
    key_file.write_text("demo-key\n")
    state = tmp_path / "watch_state.json"
    state.write_text(json.dumps({"target": "deadbeef"}))

    watch.main(["--install-hook", "--dry-run", "--settings", str(settings), "--python", PY,
                "--url", "http://192.0.2.9:8456", "--key-file", str(key_file), "--state", str(state)])
    out = capsys.readouterr().out
    assert "DOES NOT MATCH watch_state.json (deadbeef)" in out


# --- 2. the round-trip property ----------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(SETTINGS_FIXTURES))
def test_uninstall_undoes_install_byte_for_byte(name):
    original = SETTINGS_FIXTURES[name]
    raw = hi.dumps(original, 2)
    command = hi.render_command(PY, "http://127.0.0.1:8766", "/h/.engram/watch.key",
                                "/h/.engram/logs/hook.log")

    installed = hi.install(original, command)
    assert hi.find_command(installed) == command

    # every key we were not asked to touch survives the *install*, byte for byte
    for key, value in original.items():
        if key != "hooks":
            assert json.dumps(installed[key], sort_keys=True) == json.dumps(value, sort_keys=True)
    for slot, value in (original.get("hooks") or {}).items():
        if slot != hi.SLOT:
            assert json.dumps(installed["hooks"][slot], sort_keys=True) == \
                json.dumps(value, sort_keys=True)

    restored, removed = hi.uninstall(installed)
    assert removed == 1
    assert hi.dumps(restored, 2) == raw


def test_uninstall_keeps_a_third_party_session_end_entry():
    """The test that must fail if `_strip` ever widens: a neighbour in our own slot is not ours."""
    original = SETTINGS_FIXTURES["third_party_session_end"]
    command = hi.render_command(PY, "http://x", "/k", "/l")
    installed = hi.install(original, command)
    assert len(installed["hooks"][hi.SLOT]) == 2

    restored, removed = hi.uninstall(installed)
    assert removed == 1
    assert restored["hooks"][hi.SLOT] == original["hooks"][hi.SLOT]
    assert "/home/demo/end.sh" in json.dumps(restored)
    assert hi.HOOK_MODULE not in json.dumps(restored)


def test_reinstall_with_a_new_url_replaces_rather_than_doubles():
    """Two entries would mean one session fed to two servers — the failure this feature exists to avoid."""
    first = hi.install({}, hi.render_command(PY, "http://a", "/k", "/l"))
    second = hi.install(first, hi.render_command(PY, "http://b", "/k", "/l"))
    leaves = [leaf for group in second["hooks"][hi.SLOT] for leaf in group["hooks"]]
    assert len(leaves) == 1 and "http://b" in leaves[0]["command"]


def test_render_command_matches_the_agreed_shape():
    command = hi.render_command("/usr/bin/python3", "http://127.0.0.1:8766", "/h/watch.key", "/h/hook.log")
    assert command == (
        "if [ -x '/usr/bin/python3' ]; then p=$({ command -p cat 2>/dev/null || cat; }); "
        "[ -n \"$p\" ] && { printf '%s' \"$p\" | nohup '/usr/bin/python3' "
        "-m engram.connectors.session_hook --url 'http://127.0.0.1:8766' "
        "--key-file '/h/watch.key' >>'/h/hook.log' 2>&1; } & "
        "else { command -p cat 2>/dev/null || cat; } >/dev/null 2>&1 || :; fi; exit 0")
    assert hi.entry_for(command) == {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
    assert "matcher" not in json.dumps(hi.entry_for(command))  # every SessionEnd `reason` is worth feeding


def test_refuses_a_settings_file_it_cannot_reproduce(tmp_path, capsys):
    settings = tmp_path / "s.json"
    settings.write_text('{\n  // the owner writes JSONC here\n  "model": "x"\n}\n')
    before = _sha(str(settings))
    code = watch.main(["--install-hook", "--settings", str(settings), "--python", PY,
                       "--url", "http://127.0.0.1:8766", "--key-file", str(tmp_path / "k"),
                       "--state", str(tmp_path / "st.json")])
    out = capsys.readouterr().out
    assert code == watch.EXIT_USAGE
    assert "refusing to edit" in out and "by hand" in out
    assert hi.HOOK_MODULE in out                      # the entry to paste is printed, not just refused
    assert _sha(str(settings)) == before


def test_uninstall_on_a_file_without_our_entry_changes_nothing(tmp_path, capsys):
    settings = tmp_path / "s.json"
    settings.write_text(hi.dumps(SETTINGS_FIXTURES["owner_shaped"], 2))
    before = _sha(str(settings))
    code = watch.main(["--uninstall-hook", "--settings", str(settings)])
    assert code == watch.EXIT_OK
    assert "nothing to remove" in capsys.readouterr().out
    assert _sha(str(settings)) == before
    assert not list(tmp_path.glob("*.engram-bak-*"))


def test_install_then_uninstall_on_disk_round_trips(tmp_path, capsys):
    settings = tmp_path / "s.json"
    settings.write_text(hi.dumps(SETTINGS_FIXTURES["owner_shaped"], 2))
    original = settings.read_bytes()
    key_file = tmp_path / "watch.key"
    key_file.write_text("demo-key\n")

    args = ["--settings", str(settings), "--python", PY, "--url", "http://127.0.0.1:8766",
            "--key-file", str(key_file), "--state", str(tmp_path / "st.json")]
    # preflight would refuse a fake interpreter, so stub it — the real one is covered by watch_install
    import engram.connectors.watch_install as wi
    real = wi.preflight_import
    wi.preflight_import = lambda *a, **k: None
    try:
        assert watch.main(["--install-hook", *args]) == watch.EXIT_OK
    finally:
        wi.preflight_import = real
    capsys.readouterr()

    assert hi.HOOK_MODULE in settings.read_text()
    backups = list(tmp_path.glob("s.json.engram-bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original

    assert watch.main(["--uninstall-hook", "--settings", str(settings)]) == watch.EXIT_OK
    assert settings.read_bytes() == original


# --- 3. the worker ------------------------------------------------------------------------------------

def test_a_stop_payload_does_nothing(tmp_path, capsys):
    argv, _ = _hook_args(tmp_path)
    calls = []
    code = session_hook.run(argv, stdin=json.dumps({"hook_event_name": "Stop",
                                                    "transcript_path": _transcript(tmp_path)}),
                            ingest=lambda *a, **k: calls.append(a) or {})
    assert code == 0 and calls == []
    assert capsys.readouterr().out == ""  # ~23 Stops per session; a log line each would be pure noise


def test_session_end_feeds_exactly_once_and_says_so_without_naming_anything(tmp_path, capsys,
                                                                           _never_really_detach):
    path = _transcript(tmp_path)
    argv, _ = _hook_args(tmp_path)
    server = StubServer()
    calls = []

    def recording_ingest(url, key, paths, **kwargs):
        calls.append((url, key, list(paths), kwargs))
        return watch.ingest(url, key, paths, **{**kwargs, "timeout": 5})

    import engram.connectors.watch as w
    original_post = w._post
    w._post = server.post
    try:
        assert session_hook.run(argv, stdin=json.dumps(
            {"hook_event_name": "SessionEnd", "reason": "clear", "transcript_path": path}),
            ingest=recording_ingest, roots=[str(tmp_path / "roots")]) == 0
    finally:
        w._post = original_post

    assert len(calls) == 1
    url, key, paths, kwargs = calls[0]
    assert (url, key, paths) == ("http://127.0.0.1:8766", "demo-key", [path])
    assert kwargs["outcomes"] is True
    assert _never_really_detach == [1]  # detached before doing anything slow

    claims = watch.load_claims(str(tmp_path / "hook_claims.jsonl"))
    assert claims == {path: os.stat(path).st_size}
    assert oct(os.stat(tmp_path / "hook_claims.jsonl").st_mode)[-3:] == "600"

    out = capsys.readouterr().out.strip()
    assert out.count("\n") == 0 and "conclusion(s)" in out and "lag_s" in out
    for secret in (str(tmp_path), "-Users-demo-Projects-widget", os.path.basename(path), "widget"):
        assert secret not in out
    assert session_hook._path_key(path)[:12] in out


def test_a_second_run_on_an_unchanged_transcript_is_a_no_op(tmp_path, capsys):
    path = _transcript(tmp_path)
    argv, _ = _hook_args(tmp_path)
    payload = json.dumps({"hook_event_name": "SessionEnd", "transcript_path": path})
    server = StubServer()
    import engram.connectors.watch as w
    original_post = w._post
    w._post = server.post
    try:
        session_hook.run(argv, stdin=payload, roots=[str(tmp_path / "roots")])
        capsys.readouterr()
        calls = []
        session_hook.run(argv, stdin=payload, ingest=lambda *a, **k: calls.append(a) or {},
                         roots=[str(tmp_path / "roots")])
    finally:
        w._post = original_post
    assert calls == []
    assert "skipped (seen)" in capsys.readouterr().out
    assert len(server.imports) == 1


def test_a_failed_close_claims_nothing_so_the_watcher_retries(tmp_path, capsys):
    path = _transcript(tmp_path)
    argv, _ = _hook_args(tmp_path)
    server = StubServer(close_fails=True)
    import engram.connectors.watch as w
    original_post = w._post
    w._post = server.post
    try:
        session_hook.run(argv, stdin=json.dumps(
            {"hook_event_name": "SessionEnd", "transcript_path": path}),
            roots=[str(tmp_path / "roots")])
    finally:
        w._post = original_post
    assert server.imports and server.closed == []
    assert not os.path.exists(tmp_path / "hook_claims.jsonl")
    assert "nothing claimed" in capsys.readouterr().out


def test_a_subagent_transcript_is_not_the_hooks_business(tmp_path, capsys):
    path = _transcript(tmp_path)
    sub = os.path.join(os.path.dirname(path), "agent-deadbeef.jsonl")
    os.rename(path, sub)
    argv, _ = _hook_args(tmp_path)
    calls = []
    session_hook.run(argv, stdin=json.dumps({"hook_event_name": "SessionEnd", "transcript_path": sub}),
                     ingest=lambda *a, **k: calls.append(a) or {}, roots=[str(tmp_path / "roots")])
    assert calls == []
    assert "not a watched transcript" in capsys.readouterr().out


def test_a_transcript_outside_the_watched_roots_is_refused(tmp_path, capsys):
    stray = tmp_path / "elsewhere" / "notes.jsonl"
    stray.parent.mkdir()
    stray.write_text("x" * 4096)
    argv, _ = _hook_args(tmp_path)
    calls = []
    session_hook.run(argv, stdin=json.dumps({"hook_event_name": "SessionEnd",
                                             "transcript_path": str(stray)}),
                     ingest=lambda *a, **k: calls.append(a) or {}, roots=[str(tmp_path / "roots")])
    assert calls == []
    assert "not a watched transcript" in capsys.readouterr().out


# --- 4. the target guard -------------------------------------------------------------------------------

def test_a_target_mismatch_feeds_nothing_and_says_which_two(tmp_path, capsys):
    path = _transcript(tmp_path)
    argv, _ = _hook_args(tmp_path, target_in_state="0ed09d5f")
    calls = []
    code = session_hook.run(argv, stdin=json.dumps(
        {"hook_event_name": "SessionEnd", "transcript_path": path}),
        ingest=lambda *a, **k: calls.append(a) or {}, roots=[str(tmp_path / "roots")])
    out = capsys.readouterr().out
    assert code == 0 and calls == []
    assert not os.path.exists(tmp_path / "hook_claims.jsonl")
    assert out.count("\n") == 1 and "refused: target" in out and "!= 0ed09d5f" in out


def test_the_hook_never_writes_the_watchers_state_file(tmp_path):
    path = _transcript(tmp_path)
    argv, _ = _hook_args(tmp_path)
    state = tmp_path / "watch_state.json"
    state.write_text(json.dumps({"target": watch._target("http://127.0.0.1:8766", "demo-key"),
                                 "seen": {}}))
    before, before_mtime = _sha(str(state)), state.stat().st_mtime
    server = StubServer()
    import engram.connectors.watch as w
    original_post = w._post
    w._post = server.post
    try:
        session_hook.run(argv, stdin=json.dumps({"hook_event_name": "SessionEnd",
                                                 "transcript_path": path}),
                         roots=[str(tmp_path / "roots")])
    finally:
        w._post = original_post
    assert server.closed  # it really did the work
    assert _sha(str(state)) == before and state.stat().st_mtime == before_mtime


# --- 5. the handshake, and the evidence the coverage metric joins on -----------------------------------

def test_hook_close_is_visible_to_harness(tmp_path):
    """The coupling test. The hook closes a session under `_session_label(path)`; C1 defines a transcript
    as CLOSED iff that same label appears in `GET /v1/sessions`. One test, both sides."""
    path = _transcript(tmp_path)
    argv, _ = _hook_args(tmp_path)
    server = StubServer()
    import engram.connectors.watch as w
    original_post = w._post
    w._post = server.post
    try:
        session_hook.run(argv, stdin=json.dumps({"hook_event_name": "SessionEnd",
                                                 "transcript_path": path}),
                         roots=[str(tmp_path / "roots")])
    finally:
        w._post = original_post

    label = _session_label(path)
    assert server.closed == [label]
    assert label in {row["id"] for row in server.sessions()["sessions"]}


def test_the_facility_harness_can_read_this_hooks_lag_line(tmp_path, capsys):
    """The other half of the same coupling: `lag_s` in hook.log is the harness's only hook-fed lag
    source (it must never re-derive it from a path). Skipped rather than vendored, because eval/ belongs
    to the harness and this test's job is to notice when the two formats drift apart."""
    facility = pytest.importorskip("eval.facility", reason="the facility harness is not in this tree")
    if not hasattr(facility, "parse_hook_lag"):
        pytest.skip("eval/facility.py has no parse_hook_lag yet")

    path = _transcript(tmp_path)
    old = time.time() - 42
    os.utime(path, (old, old))
    argv, _ = _hook_args(tmp_path)
    server = StubServer()
    import engram.connectors.watch as w
    original_post = w._post
    w._post = server.post
    try:
        session_hook.run(argv, stdin=json.dumps({"hook_event_name": "SessionEnd",
                                                 "transcript_path": path}),
                         roots=[str(tmp_path / "roots")])
    finally:
        w._post = original_post

    hook_log = tmp_path / "hook.log"
    hook_log.write_text(capsys.readouterr().out)
    samples = facility.parse_hook_lag(str(hook_log))
    assert len(samples) == 1 and 42 <= samples[0] < 60


def test_the_watcher_treats_a_claim_as_seen_and_folds_it_under_its_lock(tmp_path):
    claims = tmp_path / "hook_claims.jsonl"
    path = str(tmp_path / "roots" / "claude" / "-x-proj" / "abc.jsonl")
    session_hook.claim(str(claims), path, 4096, "0ed09d5f")

    loaded = watch.load_claims(str(claims))
    assert loaded == {path: 4096}
    # a claim made against a different server says nothing about this one
    assert watch.load_claims(str(claims), target="ffffffff") == {}

    state: dict = {"seen": {}}
    assert watch.fold_claims(state, str(claims), target="0ed09d5f") == 1
    assert state["seen"] == {path: 4096}
    assert not claims.exists() and not (tmp_path / "hook_claims.jsonl.merging").exists()
    assert watch.fold_claims(state, str(claims)) == 0  # folding an absent file is a no-op


def test_load_claims_survives_a_hook_appending_mid_read(tmp_path):
    claims = tmp_path / "hook_claims.jsonl"
    claims.write_text(json.dumps({"path": "/a", "size": 10, "at": 1.0, "target": "t"}) + "\n"
                      + '{"path": "/b", "size": 2')  # torn final line, exactly what O_APPEND can leave
    assert watch.load_claims(str(claims)) == {"/a": 10}


def test_pending_sessions_skips_what_the_hook_already_fed(tmp_path, monkeypatch):
    path = _transcript(tmp_path)
    monkeypatch.setattr(watch, "find_sessions", lambda since=None: [path])
    old = time.time() - 10 * 3600
    os.utime(path, (old, old))
    size = os.stat(path).st_size

    assert watch.pending_sessions({}, claims=None) == [path]
    assert watch.pending_sessions({}, claims={path: size}) == []
    assert watch.pending_sessions({}, claims={path: size - 1}) == [path]  # grown since the hook fed it


# --- 6. redaction on the hook's own path ---------------------------------------------------------------

def test_a_pasted_key_is_redacted_before_the_hook_posts_anything(tmp_path):
    fake = "sk-abcdefghijklmnopqrstuvwxyz012345"
    path = _transcript(tmp_path, secret=f" here is my key {fake} keep it safe")
    argv, _ = _hook_args(tmp_path)
    server = StubServer()
    import engram.connectors.watch as w
    original_post = w._post
    w._post = server.post
    try:
        session_hook.run(argv, stdin=json.dumps({"hook_event_name": "SessionEnd",
                                                 "transcript_path": path}),
                         roots=[str(tmp_path / "roots")])
    finally:
        w._post = original_post

    body = json.dumps(server.imports)
    assert server.imports and fake not in body and "[REDACTED]" in body


# --- 7. the status line --------------------------------------------------------------------------------

def test_status_reports_the_hook_without_naming_a_transcript(tmp_path, capsys, monkeypatch):
    settings = tmp_path / "s.json"
    command = hi.render_command(PY, "http://127.0.0.1:8766", "/h/watch.key", "/h/hook.log")
    settings.write_text(hi.dumps(hi.install({}, command), 2))
    claims = tmp_path / "hook_claims.jsonl"
    session_hook.claim(str(claims), "/roots/proj/abc.jsonl", 4096, "0ed09d5f")
    monkeypatch.setattr(watch, "pending_sessions", lambda *a, **k: [])
    monkeypatch.setattr("engram.connectors.watch_install._run",
                        lambda cmd: __import__("subprocess").CompletedProcess(cmd, 1, "", ""))

    watch.main(["--status", "--settings", str(settings), "--state", str(tmp_path / "st.json"),
                "--claims", str(claims)])
    out = capsys.readouterr().out
    assert "hook        : SessionEnd installed" in out and "http://127.0.0.1:8766" in out
    assert "hook claims : 1 pending fold" in out
    assert "abc.jsonl" not in out


def test_status_says_so_when_the_hook_is_not_installed(tmp_path, capsys, monkeypatch):
    settings = tmp_path / "s.json"
    settings.write_text(hi.dumps(SETTINGS_FIXTURES["owner_shaped"], 2))
    monkeypatch.setattr(watch, "pending_sessions", lambda *a, **k: [])
    monkeypatch.setattr("engram.connectors.watch_install._run",
                        lambda cmd: __import__("subprocess").CompletedProcess(cmd, 1, "", ""))
    watch.main(["--status", "--settings", str(settings), "--state", str(tmp_path / "st.json"),
                "--claims", str(tmp_path / "none.jsonl")])
    out = capsys.readouterr().out
    assert "hook        : not installed" in out and "--install-hook" in out


def test_the_hook_flags_are_mutually_exclusive_with_the_scheduler_flags(capsys):
    with pytest.raises(SystemExit):
        watch.main(["--install-hook", "--status"])
    assert "mutually exclusive" in capsys.readouterr().err


# --- 6. the file we edit is not ours: mode and symlink ------------------------------------------------

def _stub_preflight():
    """`--install-hook` refuses a fake interpreter; these tests are about the write, not the preflight."""
    import engram.connectors.watch_install as wi
    real = wi.preflight_import
    wi.preflight_import = lambda *a, **k: None
    return wi, real


def _install_args(tmp_path, settings):
    key_file = tmp_path / "watch.key"
    key_file.write_text("demo-key\n")
    return ["--install-hook", "--settings", str(settings), "--python", PY,
            "--url", "http://127.0.0.1:8766", "--key-file", str(key_file),
            "--state", str(tmp_path / "st.json")]


def test_install_preserves_the_settings_file_permission_bits(tmp_path, capsys):
    """The owner's real settings.json is 0600, and settings.json legitimately carries an `env` block
    with tokens. Writing it back at the umask would silently make it world-readable, forever."""
    settings = tmp_path / "s.json"
    settings.write_text(hi.dumps(SETTINGS_FIXTURES["owner_shaped"], 2))
    os.chmod(settings, 0o600)

    wi, real = _stub_preflight()
    try:
        assert watch.main(_install_args(tmp_path, settings)) == watch.EXIT_OK
    finally:
        wi.preflight_import = real
    capsys.readouterr()

    assert hi.HOOK_MODULE in settings.read_text()
    assert os.stat(settings).st_mode & 0o777 == 0o600
    backups = list(tmp_path.glob("s.json.engram-bak-*"))
    assert len(backups) == 1 and os.stat(backups[0]).st_mode & 0o777 == 0o600


def test_a_new_settings_file_is_created_private(tmp_path, capsys):
    settings = tmp_path / "fresh" / "s.json"
    wi, real = _stub_preflight()
    try:
        assert watch.main(_install_args(tmp_path, settings)) == watch.EXIT_OK
    finally:
        wi.preflight_import = real
    capsys.readouterr()
    assert os.stat(settings).st_mode & 0o777 == 0o600


def test_install_follows_a_symlinked_settings_file_instead_of_replacing_it(tmp_path, capsys):
    """`~/.claude/settings.json` is commonly a symlink into a dotfiles repo. Replacing the link with a
    plain file reports success while the versioned copy never sees the entry — and the next checkout
    takes the hook away again."""
    real_file = tmp_path / "dotfiles" / "settings.json"
    real_file.parent.mkdir()
    real_file.write_text(hi.dumps(SETTINGS_FIXTURES["owner_shaped"], 2))
    os.chmod(real_file, 0o600)
    original = real_file.read_bytes()

    link = tmp_path / "s.json"
    link.symlink_to(real_file)

    wi, real = _stub_preflight()
    try:
        assert watch.main(_install_args(tmp_path, link)) == watch.EXIT_OK
    finally:
        wi.preflight_import = real
    capsys.readouterr()

    assert link.is_symlink()                                   # the link itself survived
    assert hi.HOOK_MODULE in real_file.read_text()             # the versioned file is what changed
    assert os.stat(real_file).st_mode & 0o777 == 0o600
    # The backup lives with the file it is a copy of, not next to the link.
    backups = list(real_file.parent.glob("settings.json.engram-bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert not list(tmp_path.glob("s.json.engram-bak-*"))

    assert watch.main(["--uninstall-hook", "--settings", str(link)]) == watch.EXIT_OK
    assert link.is_symlink() and real_file.read_bytes() == original


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    settings = tmp_path / "s.json"
    settings.write_text(hi.dumps(SETTINGS_FIXTURES["owner_shaped"], 2))
    original = settings.read_bytes()

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(hi.os, "replace", boom)
    with pytest.raises(OSError):
        hi.write(str(settings), "{}\n")
    assert settings.read_bytes() == original
    assert not list(tmp_path.glob("*.engram-tmp"))
