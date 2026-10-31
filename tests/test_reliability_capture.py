"""Synthetic capture/retry regressions; only a loopback HTTP stub is contacted."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import urllib.request

import pytest

from engram.connectors import session_hook, watch
from engram.service import MemoryService


@pytest.fixture(autouse=True)
def _only_loopback_http(monkeypatch):
    # Do not let a developer's configured proxy turn a deliberate dropped reply into HTTP 502.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    monkeypatch.setattr(urllib.request, "urlopen", opener.open)


def _append(path: Path, content: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n")


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic-session.jsonl"
    _append(path, "Initial synthetic conversation material. " * 70)
    return path


def _args(tmp_path: Path, url: str):
    return argparse.Namespace(url=url, key="synthetic-key", key_file="", since="", limit=25,
                              quiet_seconds=0, state=str(tmp_path / "watch_state.json"),
                              claims=str(tmp_path / "hook_claims.jsonl"), no_outcomes=False,
                              extract_facts=False, dry_run=False)


@contextmanager
def _server(service, close_reply=None, after_close=None, lose_import_reply=False):
    """Use the real HTTP client and real offline import/persistence, with a fake close provider."""
    calls = {"imports": 0, "closes": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/v1/import":
                calls["imports"] += 1
                reply = {"ok": True, **service.import_("capture-fixture", sessions=body["sessions"])}
                if lose_import_reply and calls["imports"] == 1:
                    # Server committed the import but the caller did not receive its receipt.
                    self.close_connection = True
                    return
            else:
                assert self.path == "/v1/sessions/close"
                calls["closes"] += 1
                reply = close_reply or {"ok": True, "outcomes": 0, "outcomes_status": "completed"}
                if after_close and calls["closes"] == 1:
                    after_close()
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("close_status", ["completed", "error"])
def test_growth_during_close_stays_pending_after_restart(tmp_path, monkeypatch, close_status):
    path = _source(tmp_path)
    before_size = path.stat().st_size
    service = MemoryService(data_dir=str(tmp_path / "data"), embedder_name="hashing", llm_name="")
    monkeypatch.setattr(watch, "find_sessions", lambda **_kw: [str(path)])
    tail = "The synthetic final decision arrived while close was running."
    with _server(service, {"ok": True, "outcomes": 0, "outcomes_status": close_status},
                 after_close=lambda: _append(path, tail)) as (url, _calls):
        args = _args(tmp_path, url)
        if close_status == "error":
            watch._save_state(args.state, {"target": watch._target(url, args.key),
                                          "close_failures": {str(path): watch.MAX_CLOSE_FAILURES - 1}})
        first = watch.run_once(args)
        assert tail not in "\n".join(ep.content for ep in service.get("capture-fixture").episodes_doc.values())
        # Freshly loaded state is the next process's only knowledge of the failed/successful close.
        state = watch._load_state(args.state)
        ledger = state["seen"] if close_status == "completed" else state["close_deferred"]
        assert ledger[str(path)] == before_size
        assert watch.pending_sessions(state, quiet_seconds=0) == [str(path)]
        assert first["backlog"] == 1
        second = watch.run_once(args)
        assert tail in "\n".join(ep.content for ep in service.get("capture-fixture").episodes_doc.values())
        assert second["episodes"] == 1


def test_lost_import_receipt_retries_after_restart_without_duplicate_episode(tmp_path, monkeypatch):
    path = _source(tmp_path)
    data = str(tmp_path / "data")
    service = MemoryService(data_dir=data, embedder_name="hashing", llm_name="")
    monkeypatch.setattr(watch, "find_sessions", lambda **_kw: [str(path)])
    with _server(service, lose_import_reply=True) as (url, _calls):
        args = _args(tmp_path, url)
        assert watch.run_once(args)["exit"] == watch.EXIT_UNREACHABLE
        assert str(path) not in watch._load_state(args.state).get("seen", {})
    restarted = MemoryService(data_dir=data, embedder_name="hashing", llm_name="")
    with _server(restarted) as (url, calls):
        args.url = url
        resumed = watch.run_once(args)
        assert resumed["episodes"] == 0
        assert resumed["result"]["skipped"] == 1
        assert len(list(restarted.get("capture-fixture").episodes_doc.values())) == 1
        assert watch.run_once(args)["sessions"] == 0
        assert calls == {"imports": 1, "closes": 1}


@pytest.mark.parametrize("receipt", [
    {"ok": True, "outcomes_status": "no_episodes"},
    {"ok": False, "outcomes_status": "cancelled"},
    {"ok": True, "outcomes_status": "unknown"},
    {"ok": True, "outcomes_status": None},
    {"ok": False, "outcomes_status": "completed"},
    {"outcomes_status": "completed"},
    {"ok": False},
])
def test_non_success_close_receipt_does_not_claim_transcript(tmp_path, monkeypatch, receipt):
    path = _source(tmp_path)
    service = MemoryService(data_dir=str(tmp_path / "data"), embedder_name="hashing", llm_name="")
    monkeypatch.setattr(session_hook, "_setsid", lambda: None)
    key_file = tmp_path / "synthetic.key"
    key_file.write_text("synthetic-key")
    with _server(service, {"outcomes": 0, **receipt}) as (url, _calls):
        result = watch.ingest(url, "synthetic-key", [str(path)])
        assert result["closed_ok"] == []
        assert result["close_failed"] == result["sessions_by_path"][str(path)]
        args = _args(tmp_path, url)
        session_hook.run(["--url", url, "--key-file", str(key_file), "--state", args.state,
                          "--claims", args.claims],
                         stdin=json.dumps({"hook_event_name": "SessionEnd", "transcript_path": str(path)}),
                         roots=[str(tmp_path)])
        assert watch.load_claims(args.claims) == {}
