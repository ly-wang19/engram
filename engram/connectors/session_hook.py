"""Close a Claude Code session the moment it ends, instead of waiting for the next watcher tick.

Why this exists: the watcher (connectors/watch.py) polls every 30 minutes over transcripts that have
been idle for 15 (QUIET_SECONDS), so its structural floor on "session finished -> conclusions in memory"
is 15-45 minutes, and the measured p50 on the owner's machine is far worse because the backlog is deeper
than one tick's `--limit`. A memory that can answer about a session two days later is not a memory anyone
reaches for. Claude Code fires `SessionEnd` with the transcript path; feeding that one file right there
costs exactly one `/v1/import` + one `/v1/sessions/close` — one LLM call — and brings the lag to seconds.

    # installed, never run by hand (the URL and key are baked into the settings.json command):
    engram-watch --install-hook --dry-run --settings ~/.claude/settings.json
    engram-watch --install-hook

    # what it runs, once per finished session, with the hook payload on stdin:
    python -m engram.connectors.session_hook --url URL --key-file PATH

Four rules this module exists to keep.

* **It always exits 0.** A hook that fails loudly is a hook the owner turns off, and every failure here
  is recoverable by construction: an unclaimed file is picked up by the watcher at T+15min anyway. So a
  failure is a log line and nothing else.
* **It never writes `watch_state.json`.** `watch.run_once` read-modify-writes that file under
  `watch.lock`; a hook write landing mid-tick would be silently erased by the tick's stale save — a lost
  update with no error anywhere, indistinguishable from "the hook doesn't work sometimes". It appends
  one `hook_claims.jsonl` line instead, and the watcher folds claims in under the lock it already holds
  (`watch.load_claims` / `watch.fold_claims`).
* **It refuses to feed a server the watcher does not own.** `~/.claude.json` points the MCP `engram`
  server at a public demo; a hook that resolved its target from the environment would quietly ship the
  owner's transcripts there. The `target` hash already in `watch_state.json` is the authority, and a
  mismatch is a refusal, not a warning.
* **It logs counts and rates, never content.** No path, no project name, no session title, no conclusion
  text — a transcript is identified by `sha1(path)[:12]`. (`watch.log` does print directory names; that
  is a defect this module must not copy.)

Why `SessionEnd` and not `Stop`: measured over 160 of the owner's real transcripts, a session has a mean
of 23.4 user turns (median 3, p90 87), so closing on every `Stop` means ~23 full distillation passes to
produce the same conclusions once — and `Stop` fires while the transcript is still being written, which
is exactly the moving target `QUIET_SECONDS` was written to avoid. Not `SubagentStop` either: sub-agent
transcripts are dropped at both existing layers (`agent_sessions.find_sessions`, the `isSidechain`
filter), and feeding them from a hook would contradict the ingest layer.

Why detached: not because of the hook timeout (a `"timeout": 5` entry really does buy 5s), but because
ingest+close is one LLM call of unbounded latency — and because at `SessionEnd` the CLI is exiting, so a
job supervised by the dying process has the wrong owner. `nohup` in the settings command plus `setsid()`
as this process's first act (macOS ships no `/usr/bin/setsid`).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import signal
import sys
import time
from typing import Callable, Iterable, Optional

from . import watch

DEFAULT_DEADLINE_S = 180
MAX_STDIN_BYTES = 1 << 20  # a hook payload is a few hundred bytes; anything larger is not one

# Mirrors find_sessions()' roots and its `agent-*.jsonl` / min_bytes rules. A hook that fed a file the
# watcher would never touch would put transcripts in memory that the coverage denominator cannot see.
HOOK_ROOTS = ("~/.claude/projects", "~/.codex/sessions")
MIN_BYTES = 2048

# Injectable: a test that actually called os.setsid() would move the whole pytest process into a new
# session. Everything else in this module is already parameterised, so this is the one global.
_setsid: Callable[[], int] = os.setsid


def _path_key(path: str) -> str:
    """Stable, content-free handle for a transcript. The log and the lock file both use it, so the log
    can name a file without naming the project, the worktree or the user."""
    return hashlib.sha1(path.encode("utf-8")).hexdigest()


def _say(text: str) -> None:
    """One structured line to stdout, which the settings command redirects to ~/.engram/logs/hook.log.

    Deliberately not a second log file: the shell already owns the redirect (`>>'<LOG>' 2>&1`), so a
    traceback and a result line land in the same place and in the right order.
    """
    print(f"{watch._ts()} {text}", flush=True)


def _under_roots(path: str, roots: Iterable[str]) -> bool:
    real = os.path.realpath(path)
    for root in roots:
        base = os.path.realpath(os.path.expanduser(root))
        if real == base or real.startswith(base + os.sep):
            return True
    return False


def _lock(lock_dir: str, path: str):
    """Per-transcript, non-blocking. Never `watch.lock`: that one is held for a whole tick (up to 148s
    measured), and a hook that waited on it would still be running long after the session ended."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows, where the installer is not offered
        return open(os.devnull)
    os.makedirs(lock_dir, exist_ok=True)
    fh = open(os.path.join(lock_dir, _path_key(path)[:16] + ".lock"), "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


@contextlib.contextmanager
def _deadline(seconds: int):
    """Hard bound on a hung worker. Missing it costs nothing — the file is never claimed, so the watcher
    picks it up on its next tick — which is why an alarm is safe here and would not be in the watcher."""
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _fire(_signum, _frame):
        raise TimeoutError(f"deadline of {seconds}s exceeded")

    try:
        previous = signal.signal(signal.SIGALRM, _fire)
    except ValueError:  # not the main thread; the urllib timeout is the only bound available
        yield
        return
    signal.alarm(int(seconds))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def claim(claims_path: str, path: str, size: int, target: str, now: Optional[float] = None) -> None:
    """Record that this transcript was fed, at this size, to this target.

    One `O_APPEND` line: a write this small is atomic on the platforms the installer supports, so a hook
    appending while the watcher reads can only ever leave a truncated final line (which `load_claims`
    skips) — never an interleaved one. Mode 0600 because this is the only file in the whole feature that
    holds a path.
    """
    os.makedirs(os.path.dirname(claims_path) or ".", exist_ok=True)
    line = json.dumps({"path": path, "size": int(size),
                       "at": now if now is not None else time.time(), "target": target}) + "\n"
    fd = os.open(claims_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(claims_path, 0o600)  # O_CREAT does not chmod a file that already existed


def _parse(argv: Optional[list[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m engram.connectors.session_hook",
                                 description="Feed one finished Claude Code session to Engram.")
    ap.add_argument("--url", default=watch.DEFAULT_URL)
    ap.add_argument("--key-file", default=os.path.expanduser("~/.engram/watch.key"))
    ap.add_argument("--state", default=watch.DEFAULT_STATE,
                    help="read-only: the watcher's ledger, consulted for `target` and `seen`")
    ap.add_argument("--claims", default=watch.DEFAULT_CLAIMS)
    ap.add_argument("--deadline", type=int, default=DEFAULT_DEADLINE_S)
    ap.add_argument("--max-bytes", type=int, default=watch.IMPORT_CHUNK_BYTES)
    return ap.parse_args(argv)


def run(argv: Optional[list[str]] = None, *, stdin: Optional[str] = None,
        ingest: Optional[Callable[..., dict]] = None,
        roots: Iterable[str] = HOOK_ROOTS) -> int:
    """Handle one hook payload. Always returns 0 — see the module docstring."""
    args = _parse(argv)
    ingest = ingest or watch.ingest

    raw = stdin if stdin is not None else sys.stdin.read(MAX_STDIN_BYTES)
    try:
        payload = json.loads(raw)
    except ValueError:
        return 0  # something other than a hook piped us something; say nothing
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "SessionEnd":
        # Silent, not logged: this same worker is harmless if the owner ever wires it to another slot,
        # and a log line per Stop would be ~23 lines per session of pure noise.
        return 0

    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path.strip():
        _say("hook - SessionEnd payload carried no transcript_path")
        return 0
    path = os.path.abspath(os.path.expanduser(path))
    key12 = _path_key(path)[:12]

    # Detach before doing anything slow: at SessionEnd the CLI is exiting, and a job still in its
    # session dies with it. macOS has no /usr/bin/setsid, so this is the only place it can happen.
    try:
        _setsid()
    except OSError:
        pass  # already a session leader, or no controlling terminal — nothing to detach from

    try:
        with open(args.key_file, encoding="utf-8") as fh:
            key = fh.read().strip()
    except OSError as exc:
        _say(f"hook {key12} refused: cannot read the key file ({exc.__class__.__name__})")
        return 0
    if not key:
        _say(f"hook {key12} refused: the key file is empty")
        return 0

    # The target guard. `_target` is the same field run_once uses to wipe `seen` when the server or the
    # namespace changes, so agreeing with it is exactly what "the hook and the watcher feed one memory"
    # means. The state file is opened READ-ONLY here and nowhere else in this process.
    target = watch._target(args.url, key)
    state = watch._load_state(args.state)
    installed = state.get("target")
    if installed and installed != target:
        _say(f"hook {key12} refused: target {target} != {installed} — not feeding")
        return 0

    try:
        st = os.stat(path)
    except OSError:
        _say(f"hook {key12} skipped (transcript is gone)")
        return 0
    name = os.path.basename(path)
    if not name.endswith(".jsonl") or name.startswith("agent-") or not _under_roots(path, roots):
        # `agent-*.jsonl` is a sub-agent transcript; both existing layers drop it, and so does this one.
        _say(f"hook {key12} skipped (not a watched transcript)")
        return 0
    if st.st_size < MIN_BYTES:
        _say(f"hook {key12} skipped (under {MIN_BYTES} bytes)")
        return 0
    if (state.get("seen") or {}).get(path) == st.st_size:
        _say(f"hook {key12} skipped (seen)")
        return 0
    if watch.load_claims(args.claims).get(path) == st.st_size:
        _say(f"hook {key12} skipped (seen)")
        return 0

    lock = _lock(os.path.join(os.path.dirname(args.claims) or ".", "hooks"), path)
    if lock is None:
        _say(f"hook {key12} skipped (another hook is feeding it)")
        return 0

    started = time.time()
    mtime = st.st_mtime  # the lag clock starts when the transcript stopped growing, not when we woke up
    try:
        with _deadline(args.deadline):
            result = ingest(args.url, key, [path], outcomes=True,
                            max_bytes=args.max_bytes, timeout=args.deadline)
    except Exception as exc:  # noqa: BLE001 — every failure is the watcher's problem now, not the user's
        _say(f"hook {key12} failed: {exc.__class__.__name__} — nothing claimed, "
             f"the watcher retries this file")
        return 0
    finally:
        lock.close()

    # Same "done" rule as the watcher's: a transcript counts as fed only when every session parsed out of
    # it reached the server AND was closed. A close with nothing imported returns outcomes: 0 and looks
    # like success, so importing is not evidence on its own.
    ids = (result.get("sessions_by_path") or {}).get(path) or []
    closed = set(result.get("closed_ok") or [])
    if not ids or not set(ids) <= closed:
        _say(f"hook {key12} failed: import or close did not complete — nothing claimed")
        return 0

    claim(args.claims, path, st.st_size, target)
    finished = time.time()
    _say(f"hook {key12} · {st.st_size // 1024}KB · {len(ids)} session · "
         f"{int(result.get('episodes') or 0)} episode(s) · {int(result.get('outcomes') or 0)} "
         f"conclusion(s) · lag_s {round(finished - mtime, 1)} · {round(finished - started, 1)}s")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
