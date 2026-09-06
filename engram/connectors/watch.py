"""Keep memory fed from the agent sessions already happening on this machine.

Why this exists: the memory was empty. Not "low quality" — empty. The MCP tools were wired into Claude
Code and Codex for months and `engram_remember` was never called, because it depends on an agent
*choosing* to remember mid-task. Measured across 1909 of the owner's own sessions: engram_recall /
remember / search / close_session were called zero times. Meanwhile 1600+ Claude Code transcripts and
1200+ Codex ones piled up on disk. The write path that always works is the one nobody has to remember
to use — and the one nobody has to remember to *run*, which is what the scheduler flags are for.

    engram-watch --once                       # ingest sessions touched since last run
    engram-watch --since 7d                   # or an explicit window
    engram-watch --dry-run                    # show what would be ingested
    engram-watch --install --key <api-key>    # schedule a tick every 30m (launchd / systemd / cron line)
    engram-watch --status                     # is the job loaded, when did it last feed, what's the backlog
    engram-watch --uninstall [--purge]        # remove the job (+ key file, state, lock, log)
    engram-watch --every 30m                  # or keep one foreground loop instead of a scheduler

State lives in a small JSON file next to the data dir, so re-running is cheap and idempotent: a session
already ingested at its current size is skipped, and a session that grew since last time is re-sent (the
importer's own content fingerprints drop the turns it already has).

Deliberately a batch job, not a daemon: a transcript is only worth reading once the session has gone
quiet, and a file watcher would re-ingest a conversation mid-sentence on every keystroke. The scheduler
just runs `--once` on an interval.

Cost model: sessions are posted with `metadata.source = "agent_session"` and WITHOUT `consolidate`, so
the server stores + summarizes them and distils each one at close (1 LLM call per session). Per-turn
fact extraction would be ~100 calls per session — 3108 sessions × ~100 turns ≈ 300k calls on a first
backfill, and with no LLM it produces junk (12 turns → 11 junk facts). `--extract-facts` opts in.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
from datetime import datetime, timezone
from typing import Optional

from .agent_sessions import find_sessions, load_sessions

DEFAULT_STATE = os.path.expanduser("~/.engram/watch_state.json")
DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_LIMIT = 25
DEFAULT_INTERVAL = "30m"

# A session still being written to is a moving target: ingesting it now means re-ingesting it later with
# the tail attached. Wait until it has been idle for a while.
QUIET_SECONDS = 15 * 60

# One tick's worth of transcripts does not fit in one request. The server caps a body at 2 MiB by default
# (DEFAULT_MAX_REQUEST_BYTES) and 25 real sessions serialize to ~18 MB, so the single bulk POST this used
# to send was refused with 413 — and because the server closes the connection before reading the whole
# body, urllib surfaces that as "Broken pipe", which the caller classified as a transport failure and
# retried forever. Measured on this machine: 115 identical failures over two days, nothing ingested.
# Chunking by BYTES (never by session count — sessions range from 2 KB to 2.5 MB) also buys failure
# isolation: one rejected chunk no longer costs the other sessions their import.
IMPORT_CHUNK_BYTES = 1_500_000  # under the server's 2 MiB default, with room for JSON overhead

# A path whose close keeps failing (model outage, a transcript the distiller cannot digest) is retried
# on later ticks, but not forever: after this many failures it is marked seen so one bad file cannot
# occupy a slot of every tick's `--limit` from then on.
MAX_CLOSE_FAILURES = 3

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_UNREACHABLE = 75  # EX_TEMPFAIL: nothing was marked seen, the next tick simply retries

_sleep = time.sleep  # injectable for the --every loop tests


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


def _parse_duration(value: str) -> int:
    """'7d' / '12h' / '30m' -> seconds. Bare digits are seconds."""
    value = (value or "").strip()
    if not value:
        raise SystemExit("expected a duration like 30m / 2h / 1d")
    unit = value[-1].lower()
    factor = {"d": 86400, "h": 3600, "m": 60}.get(unit)
    try:
        return int(float(value[:-1]) * factor) if factor else int(float(value))
    except ValueError:
        raise SystemExit(f"expected a duration like 30m / 2h / 1d, got {value!r}")


def _parse_since(value: str) -> Optional[float]:
    """Accept '7d' / '12h' / '30m' (a window back from now), or an epoch."""
    if not value:
        return None
    unit = value[-1].lower()
    if unit in ("d", "h", "m"):
        return time.time() - _parse_duration(value)
    try:
        return float(value)
    except ValueError:
        raise SystemExit(f"--since expects 7d / 12h / 30m or an epoch, got {value!r}")


def _oversized_key(path: str, max_bytes: int) -> str:
    try:
        return f"{os.stat(path).st_size}:{max_bytes}"
    except OSError:
        return ""


def pending_sessions(state: dict, since: Optional[float] = None,
                     quiet_seconds: int = QUIET_SECONDS,
                     now: Optional[float] = None,
                     max_bytes: int = IMPORT_CHUNK_BYTES) -> list[str]:
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
        # A transcript that cannot fit in one request is skipped rather than retried every tick — but the
        # record is keyed on (size, limit), so growing the file or raising the server's cap re-queues it.
        if (state.get("oversized") or {}).get(path) == f"{st.st_size}:{max_bytes}":
            continue
        out.append(path)
    return out


def _row_bytes(row: dict) -> int:
    return len(json.dumps({"sessions": [row]}).encode("utf-8"))


def chunk_rows(rows: list[dict], max_bytes: int = IMPORT_CHUNK_BYTES) -> tuple[list[list[dict]], list[dict]]:
    """Split import rows into request-sized batches, and name the ones that cannot fit at all.

    A session is the atom: its conclusions are distilled from the whole arc, so splitting one across two
    requests would hand the extractor half a conversation. A session larger than the budget therefore
    cannot be sent — it is returned separately so the caller can say so once instead of retrying it every
    tick until the end of time.
    """
    batches: list[list[dict]] = []
    oversized: list[dict] = []
    current: list[dict] = []
    used = 0
    for row in rows:
        size = _row_bytes(row)
        if size > max_bytes:
            oversized.append(row)
            continue
        if current and used + size > max_bytes:
            batches.append(current)
            current, used = [], 0
        current.append(row)
        used += size
    if current:
        batches.append(current)
    return batches, oversized


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
           outcomes: bool = True, extract_facts: bool = False,
           max_bytes: int = IMPORT_CHUNK_BYTES) -> dict:
    """Import the sessions, then close each one so it gets distilled.

    Importing alone only stores the transcript. The distillation — what was decided, found, learned —
    happens at close_session, so a watcher that skips the close leaves the memory full of raw turns and
    empty of conclusions, which is the state this whole path exists to fix.

    Returns the import result plus `outcomes`, `closed_ok` / `close_failed` (session ids) and
    `sessions_by_path` so the caller can mark a transcript seen only once its sessions were distilled.
    """
    # One parse per file, same cost as before — but keyed by path, because "seen" is a per-file ledger
    # and a file is only done once every session it holds has been closed.
    by_path: dict[str, list[str]] = {}
    sessions = []
    known: set[str] = set()
    for p in paths:
        by_path[p] = []
        for s in load_sessions([p]):
            by_path[p].append(s.session_id)
            if s.session_id in known:
                continue  # the same id from two files: the server dedupes, one close is enough
            known.add(s.session_id)
            sessions.append(s)
    if not sessions:
        return {"ok": True, "sessions": 0, "episodes": 0, "note": "nothing conversational in those files",
                "outcomes": 0, "closed_ok": [], "close_failed": [], "sessions_by_path": by_path}

    # Session-level time only, never per-message timestamps or file paths: the wire payload is what a
    # hosted server keeps, and `metadata.source` is what routes it away from per-turn extraction.
    rows = []
    for s in sessions:
        row: dict = {"session_id": s.session_id, "metadata": {"source": "agent_session"},
                     "messages": [{"role": m.speaker, "content": m.content} for m in s.messages]}
        if s.event_time is not None:
            row["event_time"] = s.event_time
        rows.append(row)
    batches, oversized_rows = chunk_rows(rows, max_bytes)
    oversized = [r["session_id"] for r in oversized_rows]
    sent_ids: set[str] = set()
    first_error: Optional[BaseException] = None
    result: dict = {"ok": True, "sessions": 0, "episodes": 0, "skipped": 0,
                    "facts_deferred": 0, "batches": len(batches), "import_failed": 0}
    for batch in batches:
        payload: dict = {"sessions": batch}
        if extract_facts:
            payload["consolidate"] = True
        try:
            part = _post(base_url, api_key, "/v1/import", payload, timeout)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            # One rejected batch must not cost the others their import: the sessions in it are simply not
            # marked seen, so the next tick retries exactly them. But a server that is DOWN fails every
            # batch, and that is a transport problem the caller must see as such (exit 75, ledger
            # untouched) rather than as "nothing imported today" — so the failure is re-raised below when
            # no batch got through at all.
            result["import_failed"] += len(batch)
            result.setdefault("import_errors", []).append(f"{len(batch)} session(s): {exc}")
            first_error = first_error or exc
            continue
        sent_ids.update(r["session_id"] for r in batch)
        for key in ("sessions", "episodes", "skipped", "facts_deferred"):
            result[key] = result.get(key, 0) + int(part.get(key) or 0)
    if batches and not sent_ids and first_error is not None:
        raise first_error  # nothing got through: let main classify it (transport -> 75, HTTP -> refusal)
    if oversized:
        result["oversized"] = oversized
    result.setdefault("outcomes", 0)
    result["closed_ok"], result["close_failed"] = [], []
    # A path is only "done" when every session parsed out of it actually reached the server.
    result["sessions_by_path"] = {
        path: ids for path, ids in by_path.items() if ids and set(ids) <= sent_ids
    }
    result["oversized_by_path"] = {
        path: ids for path, ids in by_path.items() if ids and set(ids) & set(oversized)
    }
    if not outcomes:
        return result

    # Distil each imported session. Per-session rather than one bulk call: the extractor reasons over a
    # single conversation's arc, and one session failing must not cost the others their conclusions.
    distilled = 0
    for s in sessions:
        if s.session_id not in sent_ids:
            continue  # never reached the server; nothing to close
        try:
            closed = _post(base_url, api_key, "/v1/sessions/close",
                           {"session_id": s.session_id, "outcomes": True}, timeout)
            distilled += int(closed.get("outcomes") or 0)
            result["closed_ok"].append(s.session_id)
        except Exception:  # noqa: BLE001 — the transcripts are already stored; a failed distil is retryable
            result["close_failed"].append(s.session_id)
    result["outcomes"] = distilled
    if result["close_failed"]:
        result["distil_failed"] = len(result["close_failed"])
    return result


def _lock(path: str):
    """Exclusive, non-blocking lock so an overlapping tick (a slow backfill still running when launchd
    fires the next one) skips instead of double-posting. Returns the open file, or None when held.
    No-op on Windows (no fcntl) — there the scheduler flags are not offered anyway."""
    try:
        import fcntl
    except ImportError:
        return open(os.devnull)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fh = open(path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _target(url: str, key: str) -> str:
    """Which server+namespace the ledger belongs to. A changed target must start from an empty `seen`:
    the old server's memory of these files says nothing about the new one's."""
    return hashlib.sha1(f"{url}|{key}".encode("utf-8")).hexdigest()[:8]


def _ts() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _log(text: str) -> None:
    print(f"{_ts()} {text}", flush=True)


def _resolve_key(args) -> str:
    """--key > --key-file > ENGRAM_API_KEY."""
    if args.key:
        return args.key
    if args.key_file:
        try:
            with open(args.key_file, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError as exc:
            raise SystemExit(f"cannot read --key-file {args.key_file}: {exc}")
    return os.environ.get("ENGRAM_API_KEY", "")


def run_once(args) -> dict:
    """One tick. Returns the run summary with an `exit` code (see EXIT_*)."""
    key = _resolve_key(args)
    if not key and not args.dry_run:
        print("set --key, --key-file or ENGRAM_API_KEY (it is the namespace this memory belongs to)")
        return {"exit": EXIT_USAGE, "error": "no key"}

    state = _load_state(args.state)
    target = _target(args.url, key)
    if state.get("target") and state.get("target") != target:
        state["seen"], state["close_failures"] = {}, {}
    # `since` is only ever the explicit window. Deriving it from `last_run` stranded every session the
    # previous tick did not reach (a `--limit 25` backfill of 3000 files would have "seen" 25 and
    # silently dropped the rest); the size ledger already makes re-scanning cheap.
    since = _parse_since(args.since) if args.since else None
    paths = pending_sessions(state, since=since, quiet_seconds=args.quiet_seconds,
                             max_bytes=getattr(args, "max_bytes", IMPORT_CHUNK_BYTES))
    backlog = len(paths)
    if args.limit:
        paths = paths[:args.limit]

    if not paths:
        _log(f"nothing new to ingest (backlog {backlog})")
        if not args.dry_run:
            state.update({"last_run": time.time(), "target": target})
            _save_state(args.state, state)
        return {"exit": EXIT_OK, "sessions": 0, "backlog": backlog}

    seen_before = state.get("seen") or {}
    fresh = sum(1 for p in paths if p not in seen_before)
    grown = len(paths) - fresh
    detail = f"{fresh} new" + (f", {grown} grown since last run" if grown else "")
    print(f"{len(paths)} session(s) to ingest ({detail}; backlog {backlog}):")
    for p in paths:
        mark = " +" if p in seen_before else ""
        print(f"  {os.path.basename(os.path.dirname(p))}/{os.path.basename(p)}{mark}")
    if args.dry_run:
        sessions = load_sessions(paths)
        turns = sum(len(s.messages) for s in sessions)
        print(f"\n[dry-run] would send {len(sessions)} session(s), {turns} turn(s) — nothing was stored")
        return {"exit": EXIT_OK, "sessions": len(sessions), "dry_run": True, "backlog": backlog}

    lock_path = os.path.join(os.path.dirname(args.state) or ".", "watch.lock")
    lock = _lock(lock_path)
    if lock is None:
        _log("already running, skipping")
        return {"exit": EXIT_OK, "skipped": "locked"}

    started = time.time()
    try:
        try:
            result = ingest(args.url, key, paths, outcomes=not args.no_outcomes,
                            extract_facts=args.extract_facts,
                            max_bytes=getattr(args, "max_bytes", IMPORT_CHUNK_BYTES))
        except urllib.error.HTTPError as exc:
            # The server answered — with a refusal. Not a transport problem, so not exit 75, but nothing
            # was stored either: leave the ledger alone and say what the server said.
            _log(f"server rejected the import ({args.url}): HTTP {exc.code}; nothing marked seen")
            state["last_result"] = {"at": started, "sessions": len(paths), "error": f"HTTP {exc.code}",
                                    "seconds": round(time.time() - started, 1)}
            state.update({"last_run": started, "target": target})
            _save_state(args.state, state)
            return {"exit": EXIT_USAGE, "error": f"HTTP {exc.code}"}
        except (urllib.error.URLError, OSError) as exc:
            _log(f"server unreachable ({args.url}): nothing marked seen, retry next tick — {exc}")
            return {"exit": EXIT_UNREACHABLE, "error": str(exc)}

        # `skipped` is the server telling us how many turns it already had: re-sending a grown session is
        # normal and cheap, because /v1/import fingerprints content rather than trusting the caller.
        seen = state.get("seen") or {}
        failures = state.get("close_failures") or {}
        closed_ok = set(result.get("closed_ok") or [])
        by_path = result.get("sessions_by_path") or {p: [] for p in paths}
        # Transcripts too large for one request: record (size, limit) so they stop occupying a slot every
        # tick, and say so once with the knob that would let them in.
        over_by_path = result.get("oversized_by_path") or {}
        if over_by_path:
            ledger = dict(state.get("oversized") or {})
            for p in over_by_path:
                ledger[p] = _oversized_key(p, getattr(args, "max_bytes", IMPORT_CHUNK_BYTES))
            state["oversized"] = ledger
            _log(f"{len(over_by_path)} transcript(s) exceed the {getattr(args, 'max_bytes', IMPORT_CHUNK_BYTES):,}-byte request budget and "
                 f"were skipped; raise ENGRAM_MAX_REQUEST_BYTES on the server and --max-bytes here to include them")
        close_failed_paths = 0
        for p in paths:
            if p in over_by_path:
                continue  # skipped above; not seen, not a close failure
            ids = by_path.get(p, [])
            if not ids:
                continue  # its batch was rejected — retry exactly these next tick
            done = args.no_outcomes or all(sid in closed_ok for sid in ids)
            if not done:
                # Not seen: the transcript is stored but not distilled, and the next tick must retry
                # the close. Bounded, so one undigestible file cannot hold a slot forever.
                n = failures.get(p, 0) + 1
                failures[p] = n
                close_failed_paths += 1
                if n < MAX_CLOSE_FAILURES:
                    continue
                failures.pop(p, None)
            else:
                failures.pop(p, None)
            try:
                seen[p] = os.stat(p).st_size
            except OSError:
                continue
        # Cap the ledger so it cannot grow without bound on a machine with thousands of transcripts.
        if len(seen) > 5000:
            seen = dict(sorted(seen.items())[-5000:])
        seconds = round(time.time() - started, 1)
        summary = {
            "at": started, "sessions": len(paths), "new": fresh, "grown": grown,
            "episodes": int(result.get("episodes") or 0), "outcomes": int(result.get("outcomes") or 0),
            "close_failed": len(result.get("close_failed") or []), "error": None, "seconds": seconds,
        }
        state.update({"seen": seen, "close_failures": failures, "last_run": started,
                      "target": target, "last_result": summary})
        _save_state(args.state, state)
        _log(f"fed {len(paths)} session(s) ({fresh} new) · {summary['episodes']} episode(s) · "
             f"{summary['outcomes']} conclusion(s) · {summary['close_failed']} close failure(s) · "
             f"{seconds}s")
        return {"exit": EXIT_OK, **summary, "result": result, "backlog": backlog - len(paths)}
    finally:
        lock.close()


def _run_every(args, interval_s: int) -> int:
    """Foreground loop for people who would rather keep a terminal open than install a scheduler."""
    try:
        while True:
            run_once(args)
            _sleep(interval_s)
    except KeyboardInterrupt:
        return EXIT_OK


# --- scheduler management --------------------------------------------------------------------------

def _job_argv(args, paths: dict) -> list[str]:
    """What the scheduled job runs. Fixed shape: no key on the command line (it is in a 0600 file),
    no per-message timestamps, no file paths beyond the state file it owns."""
    argv = ["--once", "--url", args.url, "--key-file", paths["key"], "--state", paths["state"],
            "--limit", str(args.limit if args.limit is not None else DEFAULT_LIMIT)]
    if args.since:
        argv += ["--since", args.since]
    return argv


def _pick_scheduler(name: str) -> str:
    if name != "auto":
        return name
    if sys.platform == "darwin":
        return "launchd"
    import shutil
    if sys.platform.startswith("linux") and shutil.which("systemctl"):
        return "systemd"
    return "cron"


def _install(args) -> int:
    from . import watch_install as wi

    paths = wi.paths_for()
    scheduler = _pick_scheduler(args.scheduler)
    python = args.python or sys.executable
    interval_s = _parse_duration(args.interval)
    log_path = os.path.expanduser(args.log) if args.log else paths["log"]
    argv = _job_argv(args, paths)
    key = _resolve_key(args)

    if args.dry_run or scheduler == "cron":
        if scheduler == "launchd":
            print(wi.render_launchd_plist(args.label, python, argv, interval_s, log_path))
        elif scheduler == "systemd":
            service, timer = wi.render_systemd_units(python, argv, interval_s, log_path)
            print(f"# {paths['systemd_user']}/{wi.SYSTEMD_UNIT}.service\n{service}\n"
                  f"# {paths['systemd_user']}/{wi.SYSTEMD_UNIT}.timer\n{timer}")
        else:
            # Never edit the crontab: it is the owner's file. Print the line, say where it goes.
            print(f"# add to `crontab -e` (the key is read from {paths['key']}, mode 0600):")
            print(wi.render_cron_line(python, argv, interval_s, log_path))
        if args.dry_run:
            problem = wi.preflight_import(python)
            print(f"\n[dry-run] preflight: {'ok' if not problem else 'WOULD REFUSE — ' + problem}")
            print("[dry-run] nothing was written or loaded")
            return EXIT_OK
        # cron: the printed line reads the key from the key file, so that file is the one thing this
        # path writes — otherwise the first tick fails with "cannot read --key-file".
        if key:
            wi.write_key_file(paths["key"], key)
        elif not os.path.exists(paths["key"]):
            print(f"no API key: pass --key once (stored at {paths['key']}, mode 0600)")
            return EXIT_USAGE
        return EXIT_OK

    try:
        if scheduler == "launchd":
            info = wi.install_launchd(args.label, python, argv, interval_s, log_path=log_path,
                                      key=key or None, key_path=paths["key"])
        else:
            info = wi.install_systemd(python, argv, interval_s, log_path=log_path,
                                      key=key or None, key_path=paths["key"])
    except wi.InstallError as exc:
        print(f"install failed: {exc}")
        return EXIT_USAGE
    print(f"installed {info['scheduler']} job {info['label']}")
    print(f"  interpreter : {info['python']}")
    print(f"  interval    : every {args.interval} ({info['interval_s']}s)")
    print(f"  log         : {info['log']}")
    print(f"  key file    : {info['key_file']} (0600)")
    if info.get("lint"):
        print(f"  plutil -lint: {info['lint']}")
    print(f"  loaded      : {'yes' if info.get('loaded') else 'no'}")
    print("next: engram-watch --status")
    return EXIT_OK


def _uninstall(args) -> int:
    from . import watch_install as wi

    scheduler = _pick_scheduler(args.scheduler)
    if scheduler == "launchd":
        info = wi.uninstall_launchd(args.label, purge=args.purge)
    elif scheduler == "systemd":
        info = wi.uninstall_systemd(purge=args.purge)
    else:
        paths = wi.paths_for()
        print("cron: remove the engram-watch line from `crontab -e` yourself")
        info = {"scheduler": "cron", "removed": wi._purge(paths) if args.purge else []}
    print(f"uninstalled {info['scheduler']} job; removed: {', '.join(info['removed']) or 'nothing'}")
    if info.get("unloaded") is False:
        # The files are gone but launchd has not let go of the job yet (a tick was in flight and outlived
        # the wait). Saying "uninstalled" and exiting 0 here is how a job gets left behind.
        print(f"but launchd still reports {info['label']} loaded (a tick is in flight); "
              "re-run --uninstall in a moment")
        return EXIT_USAGE
    return EXIT_OK


def _status(args) -> int:
    from . import watch_install as wi

    paths = wi.paths_for()
    scheduler = _pick_scheduler(args.scheduler)
    loaded: Optional[bool] = None
    interval_s = _parse_duration(args.interval)
    if scheduler == "launchd":
        res = wi._run(["launchctl", "print", f"{wi._domain()}/{args.label}"])
        loaded = res.returncode == 0
        plist = os.path.join(paths["launch_agents"], f"{args.label}.plist")
        if os.path.exists(plist):
            import plistlib
            with open(plist, "rb") as fh:
                interval_s = int(plistlib.load(fh).get("StartInterval") or interval_s)
    elif scheduler == "systemd":
        res = wi._run(["systemctl", "--user", "is-active", f"{wi.SYSTEMD_UNIT}.timer"])
        loaded = res.returncode == 0
    print(f"scheduler   : {scheduler} — {'loaded' if loaded else 'not loaded' if loaded is not None else 'n/a'}")

    state = _load_state(args.state)
    last = state.get("last_result") or {}
    if last:
        at = datetime.fromtimestamp(last.get("at", 0)).isoformat(timespec="seconds")
        if last.get("error"):
            print(f"last run    : {at} — error: {last['error']}")
        else:
            print(f"last run    : {at} — fed {last.get('sessions', 0)} session(s), "
                  f"{last.get('outcomes', 0)} conclusion(s), {last.get('close_failed', 0)} close failure(s), "
                  f"{last.get('seconds', 0)}s")
    else:
        print("last run    : never")
    since = _parse_since(args.since) if args.since else None
    backlog = len(pending_sessions(state, since=since, quiet_seconds=args.quiet_seconds))
    limit = args.limit or DEFAULT_LIMIT
    ticks = -(-backlog // limit) if backlog else 0
    eta = ticks * interval_s
    print(f"backlog     : {backlog} session(s) → {ticks} tick(s) at {limit}/tick, "
          f"~{eta // 3600}h{(eta % 3600) // 60:02d}m at one tick every {interval_s}s")
    return EXIT_OK


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("ENGRAM_URL") or os.environ.get("ENGRAM_API_URL")
                    or DEFAULT_URL)
    ap.add_argument("--key", default="", help="API key; precedence --key > --key-file > ENGRAM_API_KEY")
    ap.add_argument("--key-file", default="", help="read the API key from this file")
    ap.add_argument("--since", default="", help="7d / 12h / 30m; default = every unseen transcript")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="max sessions per run (keeps a first run bounded)")
    ap.add_argument("--quiet-seconds", type=int, default=QUIET_SECONDS,
                    help="how long a transcript must be idle before it counts as finished")
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--max-bytes", type=int, default=IMPORT_CHUNK_BYTES,
                    help="per-request import budget; keep it under the server's ENGRAM_MAX_REQUEST_BYTES")
    ap.add_argument("--no-outcomes", action="store_true",
                    help="import transcripts only, skip distilling them into decisions/lessons")
    ap.add_argument("--extract-facts", action="store_true",
                    help="also run per-turn fact extraction on the server (needs an LLM there; "
                         "~1 call per turn)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="run one tick (the default)")
    ap.add_argument("--every", default="", metavar="DUR",
                    help="keep running in the foreground, one tick every 30m / 2h / 1d")
    sched = ap.add_argument_group("scheduler")
    sched.add_argument("--install", action="store_true", help="install a scheduled tick")
    sched.add_argument("--uninstall", action="store_true", help="remove the scheduled tick")
    sched.add_argument("--status", action="store_true", help="is it loaded, last result, backlog")
    sched.add_argument("--scheduler", choices=["auto", "launchd", "systemd", "cron"], default="auto")
    sched.add_argument("--label", default="com.engram.watch", help="launchd label")
    sched.add_argument("--interval", default=DEFAULT_INTERVAL, help="tick interval (30m / 2h / 1d)")
    sched.add_argument("--python", default="", help="interpreter for the job (default: this one)")
    sched.add_argument("--log", default="", help="log file (default ~/.engram/logs/watch.log)")
    sched.add_argument("--purge", action="store_true",
                       help="with --uninstall: also remove the key file, state, lock and log")
    args = ap.parse_args(argv)

    modes = [m for m in ("install", "uninstall", "status") if getattr(args, m)]
    if args.every:
        modes.append("every")
    if len(modes) > 1:
        ap.error("--install / --uninstall / --status / --every are mutually exclusive")
    if args.install:
        return _install(args)
    if args.uninstall:
        return _uninstall(args)
    if args.status:
        return _status(args)
    if args.every:
        return _run_every(args, _parse_duration(args.every))
    return run_once(args)["exit"]


if __name__ == "__main__":
    sys.exit(main())
