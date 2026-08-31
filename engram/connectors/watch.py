"""Keep memory fed from the agent sessions already happening on this machine.

Why this exists: the memory was empty. Not "low quality" — empty. The MCP tools were wired into Claude
Code and Codex for months and `engram_remember` was never called, because it depends on an agent
*choosing* to remember mid-task. Meanwhile 1600+ Claude Code transcripts and 1200+ Codex ones piled up on
disk. The write path that always works is the one nobody has to remember to use.

    python -m engram.connectors.watch --once            # ingest sessions touched since last run
    python -m engram.connectors.watch --since 7d        # or an explicit window
    python -m engram.connectors.watch --dry-run         # show what would be ingested

State lives in a small JSON file next to the data dir, so re-running is cheap and idempotent: a session
already ingested at its current size is skipped, and a session that grew since last time is re-sent (the
importer's own content fingerprints drop the turns it already has).

Deliberately a batch job, not a daemon: a transcript is only worth reading once the session has gone
quiet, and a file watcher would re-ingest a conversation mid-sentence on every keystroke.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

from .agent_sessions import find_sessions, load_sessions

DEFAULT_STATE = os.path.expanduser("~/.engram/watch_state.json")

# A session still being written to is a moving target: ingesting it now means re-ingesting it later with
# the tail attached. Wait until it has been idle for a while.
QUIET_SECONDS = 15 * 60


def _load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, path)


def _parse_since(value: str) -> Optional[float]:
    """Accept '7d' / '12h' / '30m', or an epoch. None means 'whatever the state file remembers'."""
    if not value:
        return None
    unit = value[-1].lower()
    factor = {"d": 86400, "h": 3600, "m": 60}.get(unit)
    try:
        return time.time() - float(value[:-1]) * factor if factor else float(value)
    except ValueError:
        raise SystemExit(f"--since expects 7d / 12h / 30m or an epoch, got {value!r}")


def pending_sessions(state: dict, since: Optional[float] = None,
                     quiet_seconds: int = QUIET_SECONDS,
                     now: Optional[float] = None) -> list[str]:
    """Transcripts worth ingesting: changed since we last saw them, and no longer being written to."""
    now = now if now is not None else time.time()
    seen = state.get("seen") or {}
    out = []
    for path in find_sessions(since=since):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if now - st.st_mtime < quiet_seconds:
            continue  # still live; ingesting now would only force a re-ingest later
        if seen.get(path) == st.st_size:
            continue  # unchanged since the last run
        out.append(path)
    return out


def _post(base_url: str, api_key: str, path: str, body: dict, timeout: int) -> dict:
    import urllib.request

    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ingest(base_url: str, api_key: str, paths: list[str], timeout: int = 900,
           outcomes: bool = True) -> dict:
    """Import the sessions, then close each one so it gets distilled.

    Importing alone only stores the transcript. The distillation — what was decided, found, learned —
    happens at close_session, so a watcher that skips the close leaves the memory full of raw turns and
    empty of conclusions, which is the state this whole path exists to fix.
    """
    sessions = load_sessions(paths)
    if not sessions:
        return {"ok": True, "sessions": 0, "episodes": 0, "note": "nothing conversational in those files"}
    payload = {
        "sessions": [
            {"session_id": s.session_id,
             "messages": [{"role": m.speaker, "content": m.content} for m in s.messages]}
            for s in sessions
        ]
    }
    result = _post(base_url, api_key, "/v1/import", payload, timeout)
    if not outcomes:
        return result

    # Distil each imported session. Per-session rather than one bulk call: the extractor reasons over a
    # single conversation's arc, and one session failing must not cost the others their conclusions.
    distilled = 0
    failed = 0
    for s in sessions:
        try:
            closed = _post(base_url, api_key, "/v1/sessions/close",
                           {"session_id": s.session_id, "outcomes": True}, timeout)
            distilled += int(closed.get("outcomes") or 0)
        except Exception:  # noqa: BLE001 — the transcripts are already stored; a failed distil is retryable
            failed += 1
    result["outcomes"] = distilled
    if failed:
        result["distil_failed"] = failed
    return result


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("ENGRAM_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--key", default=os.environ.get("ENGRAM_API_KEY", ""))
    ap.add_argument("--since", default="", help="7d / 12h / 30m; default = since the last run")
    ap.add_argument("--limit", type=int, default=25, help="max sessions per run (keeps a first run bounded)")
    ap.add_argument("--quiet-seconds", type=int, default=QUIET_SECONDS,
                    help="how long a transcript must be idle before it counts as finished")
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--no-outcomes", action="store_true",
                    help="import transcripts only, skip distilling them into decisions/lessons")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="accepted for clarity; this always runs once")
    args = ap.parse_args(argv)

    if not args.key and not args.dry_run:
        raise SystemExit("set --key or ENGRAM_API_KEY (it is the namespace this memory belongs to)")

    state = _load_state(args.state)
    since = _parse_since(args.since) if args.since else state.get("last_run")
    paths = pending_sessions(state, since=since, quiet_seconds=args.quiet_seconds)
    if args.limit:
        paths = paths[:args.limit]

    if not paths:
        print("nothing new to ingest")
        return 0

    seen_before = state.get("seen") or {}
    fresh = sum(1 for p in paths if p not in seen_before)
    grown = len(paths) - fresh
    detail = f"{fresh} new" + (f", {grown} grown since last run" if grown else "")
    print(f"{len(paths)} session(s) to ingest ({detail}):")
    for p in paths:
        mark = " +" if p in seen_before else ""
        print(f"  {os.path.basename(os.path.dirname(p))}/{os.path.basename(p)}{mark}")
    if args.dry_run:
        sessions = load_sessions(paths)
        turns = sum(len(s.messages) for s in sessions)
        print(f"\n[dry-run] would send {len(sessions)} session(s), {turns} turn(s) — nothing was stored")
        return 0

    result = ingest(args.url, args.key, paths, outcomes=not args.no_outcomes)
    # `skipped` is the server telling us how many turns it already had: re-sending a grown session is
    # normal and cheap, because /v1/import fingerprints content rather than trusting the caller.
    print(f"\nimported: {result}")

    seen = state.get("seen") or {}
    for p in paths:
        try:
            seen[p] = os.stat(p).st_size
        except OSError:
            continue
    # Cap the ledger so it cannot grow without bound on a machine with thousands of transcripts.
    if len(seen) > 5000:
        seen = dict(sorted(seen.items())[-5000:])
    _save_state(args.state, {"seen": seen, "last_run": time.time()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
