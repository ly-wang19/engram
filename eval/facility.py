"""Is this memory a facility anyone depends on? — the L0-L4 ladder, measured off local signal.

CLAUDE.md's Bet D discipline says a number we cannot reproduce does not exist. That discipline has so
far only been pointed at *accuracy*. This points it at the other question, the one nobody was measuring:
the memory is filling fast — thousands of session conclusions on the owner's own machine — and may
barely be read. Filling a store is not a facility. Being read, and being read usefully, is.

    python eval/facility.py --days 7 --url http://127.0.0.1:8766 --key-file ~/.engram/watch.key

The ladder, as the owner set it:

    L0 exists       memory holds conclusions                                    (machine-measurable)
    L1 connected    >=90% of finished sessions closed, lag < 60s                (machine-measurable)
    L2 read         weekly engram_recall calls > 0 and trending up              (machine-measurable)
    L3 useful       (i) conclusion dedup rate > 0  (ii) >=50% of recalls changed the answer
                    (i) is machine-measurable; (ii) needs human labelling and is reported NOT MEASURED
    L4 depended on  turning it off for a week prompts turning it back on        (human judgement only)

WHY there is no `--judge` and no label queue: L3(ii) is a human judgement about whether an answer
changed. An LLM judge would put a model on the critical path of a twenty-second offline metric and would
be scoring its own family's output; a labelling queue with no consumer is abstraction for a hypothetical
future (CLAUDE.md §8). The harness prints the ceiling on usefulness (did recall return anything at all)
and says plainly that usefulness itself is unmeasured.

PRIVACY (CONTRIBUTING): the owner's transcripts carry colleague names and business detail. There is no
field in the output schema, and no line in the table, that can hold a fact, a query, a session id, a
project name or a file path. Paths are consumed in memory and never printed or written. Tests use
synthetic fixtures only.

KNOWN IMPRECISIONS, stated rather than papered over:

* `_session_label` truncates the transcript uuid to 8 hex chars, so two transcripts can collide onto one
  session id. Measured on this machine: 1 collision across 2082 transcripts (~0.05%), which very
  slightly OVERSTATES L1 coverage.
* Watcher lag is tick-resolution: watch.log's `fed` line is written after every close in a tick, so a
  session ingested early in a slow tick is credited with the whole tick's duration. Irrelevant while the
  p50 is measured in days; it matters once lag approaches the 15-45 min floor.
* L2 sees only Claude Code and Codex transcripts on this machine. It is a LOWER BOUND on Engram tool
  use, labelled "transcripts on this machine", not a global count.
* The self-development exclusion is a path substring (`--exclude-project-substr`), so it can only be as
  precise as the directory names. The number of calls it removes is always printed.

Exit codes are three-valued on purpose (`--assert-no-regress`): 0 report produced, 1 the facility
regressed, 2 the INSTRUMENT is broken (no control tool calls, server unreachable, empty roots).
Conflating 1 and 2 is how a facility metric goes quietly dead while still printing zeros.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram.connectors import watch  # noqa: E402
from engram.connectors.agent_sessions import _rows, _session_label, find_sessions  # noqa: E402
from engram.connectors.base import to_epoch  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:8766"
DEFAULT_ROOTS = "~/.claude/projects,~/.codex/sessions"
DEFAULT_STATE = "~/.engram/watch_state.json"
DEFAULT_WATCH_LOG = "~/.engram/logs/watch.log"
DEFAULT_HOOK_LOG = "~/.engram/logs/hook.log"
DEFAULT_MCP_CONFIG = "~/.claude.json"
DEFAULT_OUT = "results/facility.jsonl"

TARGET_LAG_S = 60  # the owner's L1 goal
TARGET_COVERAGE = 0.90

EXIT_OK = 0
EXIT_REGRESSED = 1
EXIT_INSTRUMENT_BROKEN = 2

# Byte prefilter applied to each raw line before json.loads. This set is load-bearing, not an
# optimisation detail: `"tool_use_id"` does NOT contain `"tool_use"` (no closing quote), so dropping
# `"tool_result"` from this set silently makes recall-reach 0 while every other number looks healthy.
_CALL_NEEDLES = (
    b'"tool_use"',
    b'"tool_result"',
    b'"function_call"',
    b'"custom_tool_call"',
    b'"local_shell_call"',
)
_CODEX_CALL_TYPES = {"function_call", "custom_tool_call", "local_shell_call"}
# Codex writes a call's RESULT back as its own payload row, joined by `call_id`. Its type names do
# not contain the call needles above (`"function_call_output"` does not contain the bytes
# `"function_call"` — no closing quote), so the result reader needs these two of its own.
_CODEX_RESULT_TYPES = {"function_call_output", "custom_tool_call_output"}
_CODEX_RESULT_NEEDLES = (b'"function_call_output"', b'"custom_tool_call_output"')
# Codex wraps an MCP tool result as "Wall time: N seconds\nOutput:\n<body>" (measured: 421 of the
# 421 MCP-shaped outputs on this machine). Stripping it is what lets one empty-recall sentinel test
# serve both agents instead of every Codex recall passing on preamble length alone.
_CODEX_OUTPUT_WRAPPER = re.compile(r"^Wall time: [0-9.]+ seconds\n(?:Output:\n)?")
_MCP_PREFIX = re.compile(r"^mcp__[^_]+(?:_[^_]+)*?__")

# engram_recall / engram_search return this exact sentence when nothing matched (engram/mcp/server.py).
_EMPTY_RECALL = "No relevant memory found for that query."
_EMPTY_RECALL_CHARS = 40  # a result shorter than this carried no retrieved context either

_WATCH_HEADER = re.compile(r"^\d+ session\(s\) to ingest")
_WATCH_FED = re.compile(r"^(\S+) fed \d+ session\(s\)")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _pct(values: list[float], q: float) -> Optional[float]:
    """Nearest-rank percentile. Returns None for an empty sample rather than inventing a 0."""
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(math.ceil(q * len(ordered))) - 1))
    return ordered[idx]


def _iso_week(ts: float) -> str:
    y, w, _ = datetime.fromtimestamp(ts).isocalendar()
    return f"{y}-W{w:02d}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _fmt_secs(seconds: Optional[float]) -> str:
    if seconds is None:
        return "n/a"
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


# ---------------------------------------------------------------------------
# server (content-free reads only)
# ---------------------------------------------------------------------------


def _get(url: str, key: str, path: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url.rstrip("/") + path,
                                 headers={"Authorization": f"Bearer {key}"}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class ServerFacts:
    """Everything the harness needs from the write target — counts only, never fact text.

    `facts_limit=0` on /v1/memories is not cosmetic: any positive limit returns the *text* of stored
    facts, which is exactly what must never enter this harness's memory, output or ledger.
    """

    def __init__(self) -> None:
        self.reachable = False
        self.error = ""
        self.episodes = 0
        self.conclusions = 0
        self.outcomes_live = 0
        self.outcomes_live_incl_sensitive = 0
        self.outcomes_superseded = 0
        self.attribute_facts = 0
        self.session_facts: dict[str, int] = {}

    @classmethod
    def load(cls, url: str, key: str) -> "ServerFacts":
        self = cls()
        try:
            stats = _get(url, key, "/v1/stats")
            counts = stats.get("counts") or {}
            self.episodes = int(counts.get("episodes") or 0)

            base = "/v1/memories?facts_limit=0&episodes_limit=0"
            mem = _get(url, key, base)
            self.conclusions = int((mem.get("counts") or {}).get("facts_outcomes") or 0)
            self.outcomes_live_incl_sensitive = self.conclusions
            live = _get(url, key, base + "&kind=outcomes&status=live")
            self.outcomes_live = int((live.get("facts_page") or {}).get("total") or 0)
            sup = _get(url, key, base + "&kind=outcomes&status=superseded")
            self.outcomes_superseded = int((sup.get("facts_page") or {}).get("total") or 0)
            attrs = _get(url, key, base + "&kind=attributes&status=live")
            self.attribute_facts = int((attrs.get("facts_page") or {}).get("total") or 0)

            offset = 0
            while True:
                page = _get(url, key, f"/v1/sessions?limit=500&offset={offset}")
                rows = page.get("sessions") or []
                for row in rows:
                    sid = row.get("id")
                    if isinstance(sid, str):
                        self.session_facts[sid] = int(row.get("facts_added") or 0)
                info = page.get("page") or {}
                nxt = info.get("next_offset")
                if not info.get("has_more") or nxt is None or not rows:
                    break
                offset = int(nxt)
            self.reachable = True
        except Exception as exc:  # noqa: BLE001 — an unreachable server is exit 2, not a crash
            self.error = f"{type(exc).__name__}: {exc}"
        return self


# ---------------------------------------------------------------------------
# transcript scanning
# ---------------------------------------------------------------------------


class Scan:
    """One pass over the transcripts in the window, producing the L1 and L2 raw material."""

    def __init__(self) -> None:
        self.files = 0
        self.bytes = 0
        self.seconds = 0.0
        self.calls: Counter = Counter()               # engram tool name -> count
        self.calls_by_week: dict[str, Counter] = defaultdict(Counter)
        self.control: Counter = Counter()             # non-engram tool name -> count
        self.excluded_selfdev_calls = 0
        self.prose_mentions = 0
        self.subagent_files = 0
        self.recall_calls = 0
        self.recall_non_empty = 0
        # Resuming a session writes a NEW transcript file that replays the whole history, tool_use
        # blocks and their ids included. Counting the replay reports reads that never happened:
        # measured on this corpus, 182 raw engram calls are only 110 distinct ones, and the whole
        # inflation lands on L2 — the rung the owner is trying to grow.
        self.seen_call_ids: set[str] = set()
        self.replayed_calls = 0


def _classify(path: str) -> str:
    """claude_code vs codex. Split by source because only one of them has an end-of-session event:
    Codex's hooks.json has no `session_end` slot, so its half of the corpus stays watcher-bound."""
    return "codex" if (f"{os.sep}.codex{os.sep}" in path
                       or os.path.basename(path).startswith("rollout-")) else "claude_code"


def _tool_calls(data: bytes) -> Iterable[tuple[str, Optional[str], Optional[float]]]:
    """Yield (tool_name, call_id, timestamp) for every structural tool call in one transcript.

    This reads `message.content` DIRECTLY rather than through agent_sessions._text_of, because that
    helper's `_SKIP_BLOCKS` deliberately discards `tool_use` and `tool_result` — the right call for
    ingest (tool machinery is not memory) and exactly wrong here, where the tool calls ARE the signal.
    """
    for line in data.splitlines():
        if not any(needle in line for needle in _CALL_NEEDLES):
            continue
        for row in _rows(line.decode("utf-8", "replace")):
            ts = to_epoch(row.get("timestamp"))
            message = row.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), list):
                for block in message["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name")
                        if isinstance(name, str):
                            yield name, block.get("id"), ts
            payload = row.get("payload")
            if isinstance(payload, dict) and payload.get("type") in _CODEX_CALL_TYPES:
                name = payload.get("name")
                if isinstance(name, str):
                    yield name, payload.get("call_id"), ts


def _tool_results(data: bytes) -> dict[str, str]:
    """call id -> result text, for the recall-reach join. BOTH agent shapes, not just Claude's.

    Gated on the SAME `_CALL_NEEDLES` set as `_tool_calls` (plus Codex's result names, which those
    needles cannot match), not on a private copy of `b'"tool_result"'`. With a private copy the
    `"tool_result"` entry up there is inert — removing it changes no number — so the comment claiming
    it is load-bearing, and the test claiming to prove it, would both describe a coupling that does
    not exist.

    Reading only Claude's shape is not a missing nicety, it is a fabricated number: a Codex
    `engram_recall` is a structural call, so it lands in the reach DENOMINATOR, but with no join it
    can never reach the numerator — reach then reads low in exact proportion to how much Codex uses
    the memory. Measured here before this join existed: 26% (13/50) where the truth is 100% (50/50).
    """
    out: dict[str, str] = {}
    for line in data.splitlines():
        if not any(needle in line for needle in _CALL_NEEDLES) \
                and not any(needle in line for needle in _CODEX_RESULT_NEEDLES):
            continue
        for row in _rows(line.decode("utf-8", "replace")):
            payload = row.get("payload")
            if isinstance(payload, dict) and payload.get("type") in _CODEX_RESULT_TYPES:
                cid = payload.get("call_id")
                body = payload.get("output")
                if isinstance(cid, str):
                    if not isinstance(body, str):
                        body = "" if body is None else json.dumps(body, ensure_ascii=False)
                    out[cid] = _CODEX_OUTPUT_WRAPPER.sub("", body)
            message = row.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                continue
            for block in message["content"]:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tid = block.get("tool_use_id")
                if not isinstance(tid, str):
                    continue
                content = block.get("content")
                if isinstance(content, list):
                    content = "\n".join(b.get("text", "") for b in content
                                        if isinstance(b, dict) and isinstance(b.get("text"), str))
                out[tid] = content if isinstance(content, str) else ""
    return out


def strip_mcp_prefix(name: str) -> str:
    """`mcp__engram__engram_recall` -> `engram_recall`; a bare tool name is returned unchanged."""
    return _MCP_PREFIX.sub("", name)


def is_engram_tool(name: str) -> bool:
    return strip_mcp_prefix(name).startswith("engram_")


def scan_transcripts(files: list[tuple[str, os.stat_result]], excludes: list[str]) -> Scan:
    """Count Engram tool calls (and a control set) across the transcripts in the window.

    `agent-*.jsonl` sub-agent transcripts are INCLUDED here and excluded from L1: a sub-agent calling
    engram_recall is a real read of the memory, but it is not a session the watcher would ever close.
    """
    scan = Scan()
    started = time.time()
    for path, st in files:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        scan.files += 1
        scan.bytes += len(data)
        if os.path.basename(path).startswith("agent-"):
            scan.subagent_files += 1
        if b"engram_" in data:
            # Every line naming an engram tool, structural or merely quoted in prose. The gap between
            # this and the structural count is the stale-matcher tripwire.
            scan.prose_mentions += sum(1 for line in data.splitlines() if b"engram_" in line)
        low = path.lower()
        excluded = any(sub in low for sub in excludes)

        results: Optional[dict[str, str]] = None
        for name, call_id, ts in _tool_calls(data):
            short = strip_mcp_prefix(name)
            if not short.startswith("engram_"):
                # The control row is counted over EVERY scanned file, not only engram-mentioning ones:
                # a control that shared the matcher's blind spots would go to zero for the same reason a
                # broken matcher does, and stop being a tripwire.
                scan.control[short] += 1
                continue
            if call_id:
                # A call id is unique per real call in both agents (measured: 0 of 182 engram calls
                # on this corpus lack one), so a repeated id is a replay, never a second read.
                if call_id in scan.seen_call_ids:
                    scan.replayed_calls += 1
                    continue
                scan.seen_call_ids.add(call_id)
            if excluded:
                scan.excluded_selfdev_calls += 1
                continue
            tool = short[len("engram_"):]
            scan.calls[tool] += 1
            scan.calls_by_week[_iso_week(ts or st.st_mtime)][tool] += 1
            if tool not in ("recall", "search"):
                continue
            # Reach: did the read return anything at all? A ceiling on usefulness, never usefulness.
            scan.recall_calls += 1
            if results is None:
                results = _tool_results(data)
            body = (results.get(call_id or "") or "").strip()
            if body and not body.startswith(_EMPTY_RECALL) and len(body) >= _EMPTY_RECALL_CHARS:
                scan.recall_non_empty += 1
    scan.seconds = round(time.time() - started, 1)
    return scan


# ---------------------------------------------------------------------------
# L1: closed-session coverage and lag
# ---------------------------------------------------------------------------


def finished_sessions(roots: list[str], since: Optional[float], quiet_seconds: int,
                      now: float) -> list[tuple[str, os.stat_result]]:
    """Transcripts eligible for ingest that have gone quiet — the L1 denominator.

    Deliberately `find_sessions()` eligibility (>=2048 bytes, no `agent-*`) plus the quiet check, i.e.
    exactly `watch.pending_sessions` minus the ledger check, so the denominator and the watcher can
    never disagree about what a session is.
    """
    out = []
    for root in roots:
        for path in find_sessions(root=root, since=since):
            try:
                st = os.stat(path)
            except OSError:
                continue
            if now - st.st_mtime < quiet_seconds:
                continue
            out.append((path, st))
    return out


def parse_watch_lag(log_path: str, state: dict, index: dict[tuple[str, str], str],
                    now: float) -> list[float]:
    """Lag samples from watch.log: close-returned-at minus transcript mtime.

    A block is the unstamped `N session(s) to ingest` header, one indented `<dir>/<file>` line per path,
    terminated by the stamped `... fed ...` line. Only `fed`-terminated blocks count — the log holds far
    more `server unreachable` lines and headers than `fed` lines, and crediting an unterminated block
    would report lag for sessions that were never stored.
    """
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    seen = state.get("seen") or {}
    samples: list[float] = []
    block: list[str] = []
    in_block = False
    for line in lines:
        if _WATCH_HEADER.match(line):
            block, in_block = [], True
            continue
        if in_block and line.startswith("  ") and "/" in line:
            block.append(line.strip().rstrip(" +"))
            continue
        fed = _WATCH_FED.match(line)
        if in_block and fed:
            fed_at = to_epoch(fed.group(1))
            if fed_at is not None:
                for key in block:
                    dirname, _, basename = key.rpartition("/")
                    path = index.get((dirname, basename))
                    if not path:
                        continue
                    try:
                        st = os.stat(path)
                    except OSError:
                        continue
                    # Only sample where the file has not moved since: the ledger recording this exact
                    # size is what makes today's mtime equal to the mtime at ingest time.
                    if seen.get(path) != st.st_size:
                        continue
                    lag = fed_at - st.st_mtime
                    if lag > 0:
                        samples.append(lag)
        block, in_block = [], False
    return samples


def parse_hook_lag(log_path: str) -> list[float]:
    """Lag samples from hook.log, which records its own `lag_s` (the hook knows both timestamps).

    Reading the hook's own number instead of re-deriving it keeps transcript paths out of this harness
    entirely for the hook-fed half of the distribution.
    """
    samples: list[float] = []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    for line in lines:
        value: Optional[float] = None
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                row = json.loads(stripped)
                if isinstance(row, dict) and row.get("lag_s") is not None:
                    value = float(row["lag_s"])
            except (ValueError, TypeError):
                value = None
        if value is None:
            m = re.search(r"lag_s[=:\s]+([0-9]+(?:\.[0-9]+)?)", line)
            if m:
                value = float(m.group(1))
        if value is not None and value > 0:
            samples.append(value)
    return samples


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _read_target(mcp_config: str) -> tuple[str, str]:
    """The URL+key the owner's agents actually READ from. Read-only; never edited by this harness."""
    try:
        with open(os.path.expanduser(mcp_config), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return "", ""
    server = ((data.get("mcpServers") or {}).get("engram") or {})
    env = server.get("env") or {}
    return str(env.get("ENGRAM_API_URL") or ""), str(env.get("ENGRAM_API_KEY") or "")


def _previous_line(out_path: str, window_days: Optional[int]) -> Optional[dict]:
    """The most recent ledger line for the SAME window.

    Comparing a `--days 7` run against an `--all` run would report a coverage cliff every time the
    window changed: coverage is ~100% above the drain frontier and ~0% below it, so the two windows are
    not the same measurement and must not be differenced.
    """
    last = None
    try:
        with open(out_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("window_days") == window_days:
                    last = row
    except OSError:
        return None
    return last


def build_report(args) -> dict:
    now = time.time()
    roots = [os.path.expanduser(r.strip()) for r in args.roots.split(",") if r.strip()]
    roots = [r for r in roots if os.path.isdir(r)]
    since = None if args.all else now - args.days * 86400
    excludes = [s.lower() for s in (args.exclude_project_substr or [])]

    key = args.key or ""
    if not key and args.key_file:
        try:
            with open(os.path.expanduser(args.key_file), encoding="utf-8") as fh:
                key = fh.read().strip()
        except OSError:
            key = ""
    if not key:
        key = os.environ.get("ENGRAM_API_KEY", "")

    server = ServerFacts.load(args.url, key)
    state = watch._load_state(os.path.expanduser(args.state))

    # --- L1 -----------------------------------------------------------------
    finished = finished_sessions(roots, since, args.quiet_seconds, now)
    by_source: dict[str, dict] = {"claude_code": {"finished": 0, "closed": 0},
                                  "codex": {"finished": 0, "closed": 0}}
    closed = distilled = 0
    for path, _st in finished:
        source = _classify(path)
        by_source.setdefault(source, {"finished": 0, "closed": 0})
        by_source[source]["finished"] += 1
        label = _session_label(path)
        if label in server.session_facts:
            closed += 1
            by_source[source]["closed"] += 1
            if server.session_facts[label] > 0:
                distilled += 1
    for row in by_source.values():
        row["coverage"] = _rate(row["closed"], row["finished"])

    # The watcher's own view of what is still owed, so `claims` has to be folded in the same way
    # run_once folds it: a transcript the SessionEnd hook already fed is not backlog, and counting it
    # would overstate the drain ETA for as long as the claims file has not been folded. Note this is
    # always the watcher's DEFAULT roots (pending_sessions takes no root), not --roots.
    backlog = len(watch.pending_sessions(state, quiet_seconds=args.quiet_seconds, now=now,
                                         claims=watch.load_claims(watch.DEFAULT_CLAIMS)))
    tick_s = watch._parse_duration(args.tick_interval)
    limit = watch.DEFAULT_LIMIT
    # A FLOOR, not a forecast: it assumes every tick ingests a full `limit` of NEW sessions. The log
    # says otherwise — backlog ran 2064 -> 1596 over 39 ticks here, ~12/tick net, because each tick
    # spends part of its budget re-feeding grown transcripts while new sessions keep arriving. Printed
    # with ">=" for that reason; anything tighter is a number watch.log contradicts.
    ticks = -(-backlog // limit) if backlog else 0
    drain_eta_h = round(ticks * tick_s / 3600.0, 1)

    # The ledger is a claim, not evidence (it also records give-ups after MAX_CLOSE_FAILURES). Print the
    # delta between what it claims and what the server actually holds.
    seen = state.get("seen") or {}
    ledger_done = ledger_disagree = 0
    for path, size in seen.items():
        try:
            if os.stat(path).st_size != size:
                continue
        except OSError:
            continue
        ledger_done += 1
        if _session_label(path) not in server.session_facts:
            ledger_disagree += 1

    # watch.log names each path as `<parent-dirname>/<basename>` — enough to join back to a path, and
    # the only reason this harness touches path strings at all. Built from --roots so the join can be
    # exercised against a fixture tree.
    index = {(os.path.basename(os.path.dirname(p)), os.path.basename(p)): p
             for root in roots for p in find_sessions(root=root)}
    watch_lag = parse_watch_lag(os.path.expanduser(args.watch_log), state, index, now)
    hook_lag = parse_hook_lag(os.path.expanduser(args.hook_log))
    all_lag = watch_lag + hook_lag

    # --- L2 -----------------------------------------------------------------
    scan_files: list[tuple[str, os.stat_result]] = []
    for root in roots:
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if since is not None and st.st_mtime < since:
                    continue
                scan_files.append((path, st))
    scan = scan_transcripts(scan_files, excludes)

    # The current ISO week is always present, zero-filled if nothing was called. "Weekly recall > 0 and
    # trending up" is a claim about NOW; letting the series end at the last week that happened to have a
    # call would read a June spike as a healthy trend forever.
    scan.calls_by_week.setdefault(_iso_week(now), Counter())
    weeks = {week: {"recall": c.get("recall", 0), "remember": c.get("remember", 0),
                    "close_session": c.get("close_session", 0),
                    "other": sum(v for k, v in c.items()
                                 if k not in ("recall", "remember", "close_session"))}
             for week, c in sorted(scan.calls_by_week.items())}

    # --- rungs ---------------------------------------------------------------
    coverage = _rate(closed, len(finished))
    lag_p50 = _pct(all_lag, 0.5)
    recall_week_counts = [w["recall"] for _k, w in sorted(weeks.items())]
    l2_met = bool(recall_week_counts) and recall_week_counts[-1] > 0 and (
        len(recall_week_counts) < 2 or recall_week_counts[-1] >= recall_week_counts[-2])
    supersession_rate = _rate(server.outcomes_superseded,
                              server.outcomes_superseded + server.outcomes_live)
    rungs = {
        "L0": "met" if server.conclusions > 0 and server.episodes > 0 else "not_met",
        "L1": "met" if (coverage >= TARGET_COVERAGE and lag_p50 is not None
                        and lag_p50 < TARGET_LAG_S) else "not_met",
        "L2": "met" if l2_met else "not_met",
        # L3 needs BOTH halves; (ii) has no machine proxy, so the rung can never read "met" from here.
        "L3": "not_met" if supersession_rate == 0 else "not_measurable",
        "L4": "not_measurable",
    }

    read_url, read_key = _read_target(args.mcp_config)
    last_result = state.get("last_result") or {}
    last_fed_at = last_result.get("at")

    return {
        "run_id": _now_iso(),
        "window_days": None if args.all else args.days,
        "scan": {"files": scan.files, "bytes": scan.bytes, "seconds": scan.seconds},
        "targets": {
            "write_url": args.url,
            "write_target": watch._target(args.url, key),
            "read_url": read_url,
            "read_target": watch._target(read_url, read_key) if read_url else "",
            "same_memory": bool(read_url) and read_url == args.url and read_key == key,
        },
        "rungs": rungs,
        "l0": {"episodes": server.episodes, "conclusions": server.conclusions,
               "fed_sessions": len(server.session_facts),
               "last_fed_age_h": round((now - last_fed_at) / 3600.0, 1) if last_fed_at else None},
        "l1": {
            "by_source": by_source,
            "finished": len(finished), "closed": closed, "coverage": coverage,
            "distilled": distilled, "yield": _rate(distilled, closed),
            "backlog": backlog, "drain_eta_h": drain_eta_h,
            "ledger_done": ledger_done, "ledger_disagree": ledger_disagree,
            "lag": {
                "n": len(all_lag), "p50_s": _pct(all_lag, 0.5), "p95_s": _pct(all_lag, 0.95),
                "min_s": min(all_lag) if all_lag else None,
                "by_source": {
                    "watch": {"n": len(watch_lag), "p50_s": _pct(watch_lag, 0.5)},
                    "hook": {"n": len(hook_lag), "p50_s": _pct(hook_lag, 0.5)},
                },
            },
            "lag_floor_watch_s": [args.quiet_seconds, args.quiet_seconds + tick_s],
            "target_lag_s": TARGET_LAG_S,
            "target_lag_reachable_watch": False,  # QUIET_SECONDS alone is 15x the 60s goal
            "target_lag_reachable_hook": True,
        },
        "l2": {
            "weeks": weeks,
            "calls_total": int(sum(scan.calls.values())),
            "by_tool": dict(scan.calls.most_common()),
            "control": dict(scan.control.most_common(3)),
            "control_total": int(sum(scan.control.values())),
            "control_distinct": len(scan.control),
            "excluded_selfdev_calls": scan.excluded_selfdev_calls,
            "replayed_calls": scan.replayed_calls,
            "subagent_files_included": scan.subagent_files > 0,
            "prose_mentions": scan.prose_mentions,
        },
        "l3": {
            "outcomes_live": server.outcomes_live,
            "outcomes_live_incl_sensitive": server.outcomes_live_incl_sensitive,
            "outcomes_superseded": server.outcomes_superseded,
            "supersession_rate": supersession_rate,
            "attribute_facts": server.attribute_facts,
            "recall_calls": scan.recall_calls,
            "recall_non_empty": scan.recall_non_empty,
            "recall_reach": _rate(scan.recall_non_empty, scan.recall_calls),
            "useful_rate": None,
            "useful_note": "not measured — needs human labelling; no machine proxy",
        },
        "l4": {"measurable": False, "last_ab": None},
        "_instrument": {"server_reachable": server.reachable, "server_error": server.error,
                        "roots": len(roots)},
    }


def render(report: dict) -> str:
    t, l0, l1, l2, l3 = (report["targets"], report["l0"], report["l1"], report["l2"], report["l3"])
    lag = l1["lag"]
    window = "all time" if report["window_days"] is None else f"{report['window_days']}d window"
    lines = []
    lines.append(f"engram facility · {report['run_id']} · {window} · "
                 f"scan {report['scan']['files']} files / "
                 f"{report['scan']['bytes'] / 1e9:.2f} GB / {report['scan']['seconds']}s")
    marker = "" if t["same_memory"] else "   <-- DIFFERENT MEMORY"
    lines.append(f"  write target : {t['write_url']} [{t['write_target']}]")
    lines.append(f"  read  target : {t['read_url'] or '(no engram MCP server configured)'} "
                 f"[{t['read_target']}]{marker}")
    lines.append("")
    lines.append(f"L0 exists      {report['rungs']['L0'].upper():<14} "
                 f"{l0['conclusions']} conclusions · {l0['episodes']} episodes · "
                 f"{l0['fed_sessions']} fed sessions · last fed "
                 f"{'never' if l0['last_fed_age_h'] is None else str(l0['last_fed_age_h']) + 'h ago'}")
    src = " · ".join(f"{name} {row['closed']}/{row['finished']} ({row['coverage'] * 100:.1f}%)"
                     for name, row in sorted(l1["by_source"].items()))
    lines.append(f"L1 connected   {report['rungs']['L1'].upper():<14} "
                 f"coverage {l1['closed']}/{l1['finished']} = {l1['coverage'] * 100:.1f}% "
                 f"(goal {TARGET_COVERAGE * 100:.0f}%)")
    lines.append(f"                             by source: {src}")
    lines.append(f"                             yield {l1['distilled']}/{l1['closed']} = "
                 f"{l1['yield'] * 100:.1f}% distilled · backlog {l1['backlog']} "
                 f"(drain ETA >={l1['drain_eta_h']}h at {watch.DEFAULT_LIMIT}/tick; observed "
                 f"net drain is slower) · ledger {l1['ledger_done']} done, "
                 f"{l1['ledger_disagree']} disagree")
    lines.append(f"                             lag n={lag['n']} min {_fmt_secs(lag['min_s'])} "
                 f"p50 {_fmt_secs(lag['p50_s'])} p95 {_fmt_secs(lag['p95_s'])} "
                 f"(goal {TARGET_LAG_S}s)")
    lines.append(f"                             watch n={lag['by_source']['watch']['n']} "
                 f"p50 {_fmt_secs(lag['by_source']['watch']['p50_s'])} · "
                 f"hook n={lag['by_source']['hook']['n']} "
                 f"p50 {_fmt_secs(lag['by_source']['hook']['p50_s'])} · "
                 f"watcher floor {_fmt_secs(l1['lag_floor_watch_s'][0])}-"
                 f"{_fmt_secs(l1['lag_floor_watch_s'][1])} → 60s reachable: "
                 f"watch={l1['target_lag_reachable_watch']} hook={l1['target_lag_reachable_hook']}")
    weeks = " · ".join(f"{w} recall {c['recall']}/remember {c['remember']}/close {c['close_session']}"
                       f"/other {c['other']}" for w, c in sorted(l2["weeks"].items())) or "(none)"
    lines.append(f"L2 read        {report['rungs']['L2'].upper():<14} "
                 f"{l2['calls_total']} engram call(s) in transcripts on this machine "
                 f"(lower bound; {l2['excluded_selfdev_calls']} self-dev, "
                 f"{l2.get('replayed_calls', 0)} resume-replay calls excluded)")
    lines.append(f"                             {weeks}")
    ctrl = " · ".join(f"{k} {v}" for k, v in l2["control"].items()) or "(none)"
    lines.append(f"                             control: {ctrl} "
                 f"({l2['control_total']} calls, {l2['control_distinct']} distinct tools)")
    if l2["prose_mentions"] and not l2["calls_total"] and not l2["excluded_selfdev_calls"]:
        lines.append(f"                             !! matcher may be stale: {l2['prose_mentions']} "
                     "lines mention engram_* but 0 structural calls")
    lines.append(f"L3 useful      {report['rungs']['L3'].upper():<14} "
                 f"supersession {l3['outcomes_superseded']}/{l3['outcomes_live']} = "
                 f"{l3['supersession_rate'] * 100:.1f}% "
                 f"({l3['outcomes_live_incl_sensitive']} incl. sensitive) · "
                 f"attribute facts {l3['attribute_facts']}")
    if l3["supersession_rate"] == 0:
        # Only while the rate really is 0: a hard-coded explanation that outlives the number it explains
        # is worse than none, because it tells the reader not to look.
        lines.append("                             reason: conclusions are keyed subject=session_id, so "
                     "no two ever contend")
        lines.append("                                     for a (subject, predicate) slot — 0% is a "
                     "design consequence, not a tuning failure")
    lines.append(f"                             recall reach {l3['recall_non_empty']}/"
                 f"{l3['recall_calls']} = {l3['recall_reach'] * 100:.0f}% "
                 "(a CEILING on usefulness, not usefulness)")
    lines.append(f"                             useful_rate: {l3['useful_note']}")
    lines.append(f"L4 depended on {report['rungs']['L4'].upper():<14} "
                 "not measurable by machine — needs an off/on week; last A/B: never")
    return "\n".join(lines)


def check_regression(report: dict, previous: Optional[dict]) -> tuple[int, list[str]]:
    """exit 2 = the instrument is broken; exit 1 = the facility regressed. Never conflate them."""
    broken = []
    if not report["_instrument"]["server_reachable"]:
        broken.append("server unreachable")
    if not report["_instrument"]["roots"]:
        broken.append("no transcript roots found")
    if report["l2"]["control_total"] == 0:
        broken.append("control tool count is 0 — the tool-call matcher is not seeing anything")
    if broken:
        return EXIT_INSTRUMENT_BROKEN, broken

    if not previous:
        return EXIT_OK, []
    regressed = []
    prev_cov = (previous.get("l1") or {}).get("coverage")
    if isinstance(prev_cov, (int, float)) and report["l1"]["coverage"] < prev_cov - 0.05:
        regressed.append(f"L1 coverage {prev_cov * 100:.1f}% -> {report['l1']['coverage'] * 100:.1f}%")
    prev_lag = ((previous.get("l1") or {}).get("lag") or {}).get("p50_s")
    now_lag = report["l1"]["lag"]["p50_s"]
    if isinstance(prev_lag, (int, float)) and prev_lag > 0 and isinstance(now_lag, (int, float)) \
            and now_lag > 2 * prev_lag:
        regressed.append(f"lag p50 {_fmt_secs(prev_lag)} -> {_fmt_secs(now_lag)}")
    for rung, value in report["rungs"].items():
        if (previous.get("rungs") or {}).get(rung) == "met" and value != "met":
            regressed.append(f"{rung} dropped from met")
    prev_recall = sum(w.get("recall", 0) for w in ((previous.get("l2") or {}).get("weeks") or {}).values())
    now_recall = sum(w["recall"] for w in report["l2"]["weeks"].values())
    if prev_recall > 0 and now_recall == 0:
        regressed.append("weekly recall fell to 0")
    return (EXIT_REGRESSED, regressed) if regressed else (EXIT_OK, [])


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("ENGRAM_URL") or DEFAULT_URL,
                    help="write target (the server the watcher/hook feeds)")
    ap.add_argument("--key", default="", help="API key; precedence --key > --key-file > ENGRAM_API_KEY")
    ap.add_argument("--key-file", default="", help="read the API key from this file")
    ap.add_argument("--days", type=int, default=7, help="scan window in days (default 7)")
    ap.add_argument("--all", action="store_true", help="scan the whole corpus instead of a window")
    ap.add_argument("--roots", default=DEFAULT_ROOTS)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--watch-log", default=DEFAULT_WATCH_LOG)
    ap.add_argument("--hook-log", default=DEFAULT_HOOK_LOG)
    ap.add_argument("--quiet-seconds", type=int, default=watch.QUIET_SECONDS)
    ap.add_argument("--tick-interval", default=watch.DEFAULT_INTERVAL,
                    help="scheduler interval; sets the watcher's lag floor")
    ap.add_argument("--exclude-project-substr", action="append", default=None,
                    help="repeatable; transcripts whose path contains this are self-development "
                         "(default: super-memory)")
    ap.add_argument("--mcp-config", default=DEFAULT_MCP_CONFIG,
                    help="read-only, to report which memory the agents actually read from")
    ap.add_argument("--json", action="store_true", help="print the JSON object instead of the table")
    ap.add_argument("--out", default=DEFAULT_OUT, help="append one JSON line per run")
    ap.add_argument("--no-out", action="store_true", help="do not append to the ledger")
    ap.add_argument("--assert-no-regress", action="store_true",
                    help="exit 1 if the facility regressed, 2 if the instrument is broken")
    args = ap.parse_args(argv)
    if args.exclude_project_substr is None:
        args.exclude_project_substr = ["super-memory"]

    report = build_report(args)
    previous = (_previous_line(args.out, report["window_days"])
                if args.out and not args.no_out else None)
    code, reasons = check_regression(report, previous)

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(render(report))
        if reasons:
            label = "INSTRUMENT BROKEN" if code == EXIT_INSTRUMENT_BROKEN else "REGRESSED"
            print(f"\n{label}: " + "; ".join(reasons))

    if args.out and not args.no_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False) + "\n")

    return code if args.assert_no_regress else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
