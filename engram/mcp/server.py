"""engram_mcp — the Model Context Protocol server for Engram long-term memory (CLAUDE.md §6, the
highest-leverage adoption surface for 2026 agent stacks).

Give any MCP client (Claude Desktop, Claude Code, Cursor, …) a persistent, bi-temporal memory: it can
`engram_remember` facts across sessions and `engram_recall` a small, relevant slice to ground its next
answer — instead of restuffing the whole history.

Run it (stdio, for a desktop client):
    pip install "engram-memory[mcp]"
    python -m engram.mcp                       # local memory at ~/.engram/data, namespace 'me'
    ENGRAM_API_URL=http://localhost:8000 ENGRAM_API_KEY=sk-… python -m engram.mcp   # proxy a server

The tools are thin formatters over the shared MemoryService / HTTP contract (see engram/mcp/backends.py),
so local and hosted deployments behave identically.
"""
# NOTE: no `from __future__ import annotations` here — FastMCP introspects these tool signatures at
# runtime (issubclass checks on each param's annotation), which a stringified annotation would break.

import json
from enum import Enum
from typing import Annotated, Optional

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    from pydantic import Field
except Exception as exc:  # noqa: BLE001
    raise SystemExit('the MCP server needs the MCP SDK — `pip install "engram-memory[mcp]"`') from exc

from .backends import Backend, make_backend

INSTRUCTIONS = (
    "Engram is the user's long-term memory. Use `engram_recall` BEFORE answering anything that may "
    "depend on what you've learned about the user in past sessions (their preferences, projects, "
    "people, decisions) — it returns a small, dated, relevant context to ground your answer. Use "
    "`engram_remember` whenever the user states a durable fact, preference, or decision worth keeping "
    "(use scope='long' or the default scope='auto'); use scope='working' for temporary current-session "
    "state that should not become a long-term profile fact. "
    "Use `engram_close_session` when a thread/task ends to finish consolidation and clear ephemeral "
    "working state for that session; use `engram_session_report` when the user asks what that session "
    "saved; use `engram_list_sessions` when the user asks which agent/app sessions touched memory. "
    "`engram_search` gives a single direct answer; `engram_agent_status` checks the current namespace, "
    "session, focus, and next memory actions without exposing memory contents; `engram_stats` checks "
    "content-free memory health; "
    "`engram_list_facts` / `engram_profile` browse what's stored; `engram_add_fact` records an "
    "authoritative fact; `engram_update_fact` corrects a specific fact; `engram_delete_fact` deletes "
    "one specific fact; `engram_get_focus` / `engram_set_focus` inspect and update the user's tracked "
    "or muted memory topics; `engram_import` bulk-loads an exported history; `engram_export` gives the "
    "user a portable memory export; `engram_forget` erases everything (irreversible)."
)

mcp = FastMCP("engram_mcp", instructions=INSTRUCTIONS)

# --- backend singleton (overridable for tests / embedding) ------------------
_backend: Optional[Backend] = None


def backend() -> Backend:
    global _backend
    if _backend is None:
        _backend = make_backend()
    return _backend


def set_backend(b: Optional[Backend]) -> None:
    """Inject a backend (tests, or embedding the server in another process)."""
    global _backend
    _backend = b


# --- shared formatting + error helpers --------------------------------------
class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class RememberScope(str, Enum):
    AUTO = "auto"
    LONG = "long"
    WORKING = "working"


def _json(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _err(e: Exception) -> str:
    """Actionable, non-leaky error text (best-practice: guide the agent to a fix)."""
    try:
        import httpx

        if isinstance(e, httpx.HTTPStatusError):
            code = e.response.status_code
            if code == 401:
                return ("Error: the Engram server rejected the credentials (401). Check ENGRAM_API_KEY "
                        "matches a configured key on the server.")
            if code == 404:
                return "Error: the Engram server has no such resource (404)."
            if code == 400:
                return f"Error: the Engram server rejected the request (400): {e.response.text[:200]}"
            return f"Error: the Engram server returned HTTP {code}."
        if isinstance(e, httpx.ConnectError):
            return ("Error: could not reach the Engram server at ENGRAM_API_URL. Is it running "
                    "(uvicorn engram.server.app:app)?")
    except Exception:  # noqa: BLE001 — httpx may be absent in pure-local mode
        pass
    return f"Error: {type(e).__name__}: {e}"


def _format_focus(focus: dict) -> str:
    track = focus.get("track") or []
    mute = focus.get("mute") or []
    track_s = ", ".join(track) if track else "(none)"
    mute_s = ", ".join(mute) if mute else "(none)"
    return f"- Track: {track_s}\n- Mute: {mute_s}"


# annotation presets
_READ = dict(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_WRITE = dict(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
_DESTRUCTIVE = dict(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool(name="engram_recall", annotations=ToolAnnotations(title="Recall relevant memory", **_READ))
async def engram_recall(
    query: Annotated[str, Field(description="What you want to remember about the user / past sessions, "
                                "e.g. 'the user's dietary restrictions' or 'what we decided about the API'.",
                                min_length=1)],
    max_chunks: Annotated[int, Field(description="How many full past conversations to include for detail "
                                     "(0 = facts/summaries only). Default 6.", ge=0, le=20)] = 6,
    session_id: Annotated[Optional[str], Field(description="Optional conversation/thread id; when set, "
                                           "session-scoped working memory is included.")] = None,
    as_of: Annotated[Optional[float], Field(description="Optional epoch seconds for a point-in-time memory "
                                           "view, e.g. what was believed before a later update.")] = None,
    redact_sensitive: Annotated[bool, Field(description="When true, omit facts tagged sensitive from the "
                                            "returned context.")] = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """Retrieve a SMALL, relevant, dated slice of the user's long-term memory to ground your answer.

    This is the primary read tool — call it before answering anything that might depend on prior
    sessions. It returns the user's profile + the most relevant dated facts + session summaries + a few
    full conversations, already token-budgeted (NOT the whole history). Read the returned context and
    answer from it; if it's empty, the memory has nothing on the topic.

    Returns (markdown): a context block to read from, with an approximate token count.
    Returns (json): {"context": str, "tokens_est": int, "as_of": float|null, "redacted_sensitive": bool}.
    """
    try:
        data = await backend().recall(
            query,
            n_chunks=max_chunks,
            lean=True,
            session_id=session_id,
            as_of=as_of,
            redact_sensitive=redact_sensitive,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if response_format == ResponseFormat.JSON:
        return _json(data)
    ctx = (data.get("context") or "").strip()
    if not ctx:
        return "No relevant memory found for that query."
    return f"Relevant memory (~{data.get('tokens_est', '?')} tokens):\n\n{ctx}"


@mcp.tool(name="engram_search", annotations=ToolAnnotations(title="Answer from memory", **_READ))
async def engram_search(
    query: Annotated[str, Field(description="A direct factual question, e.g. 'Where does the user work?'",
                                min_length=1)],
    as_of: Annotated[Optional[float], Field(description="Optional epoch seconds for a point-in-time memory "
                                           "view, e.g. answer as of a past date.")] = None,
    redact_sensitive: Annotated[bool, Field(description="When true, omit facts tagged sensitive and abstain "
                                            "instead of returning sensitive answers.")] = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """Answer a single factual question directly from memory (with the supporting facts).

    Use this when you want ONE concrete answer rather than a context block (use `engram_recall` for
    broader grounding). It abstains ("I don't have that in memory.") instead of guessing when the
    attribute isn't stored.

    Returns (markdown): the answer plus the facts it rests on.
    Returns (json): {"answer": str, "facts": [str, ...], "as_of": float|null, "redacted_sensitive": bool}.
    """
    try:
        data = await backend().recall(query, lean=False, as_of=as_of, redact_sensitive=redact_sensitive)
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if response_format == ResponseFormat.JSON:
        return _json(data)
    lines = [f"**Answer:** {data.get('answer') or '(no answer)'}"]
    facts = data.get("facts") or []
    if facts:
        lines += ["", "Supporting facts:"] + [f"- {t}" for t in facts]
    return "\n".join(lines)


@mcp.tool(name="engram_remember", annotations=ToolAnnotations(title="Remember a memory", **_WRITE))
async def engram_remember(
    content: Annotated[str, Field(description="The message / fact / preference to store, in natural "
                                  "language. e.g. 'I just moved to Berlin and started a new job at Acme.'",
                                  min_length=1)],
    session_id: Annotated[str, Field(description="Optional conversation id to group related turns.")] = "default",
    scope: Annotated[RememberScope, Field(description="auto = route by ephemerality; long = force durable "
                                           "fact extraction; working = current-session state that should not "
                                           "be promoted to long-term profile facts.")] = RememberScope.AUTO,
) -> str:
    """Store something in the user's long-term memory and consolidate it (extract atomic bi-temporal
    facts, resolve contradictions against what's already known, update the knowledge graph).

    Call this whenever the user states a durable fact, preference, plan, or decision worth remembering
    across sessions. Use `scope=working` for temporary current-session state. Storing a value that
    contradicts an older one does NOT delete history — the old fact is invalidated and kept (ask-as-of
    still works). Best-effort: the raw memory is saved even if consolidation degrades.
    """
    try:
        data = await backend().remember(content, session_id=session_id, scope=scope.value)
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if data.get("degraded"):
        return (f"Stored the raw memory, but consolidation degraded ({data['degraded']}). It is still "
                "recallable.")
    if data.get("scope") == "working":
        return (
            "Stored as working memory for this session. It remains in dated history, but will not be "
            "promoted to durable profile facts."
        )
    return (f"Remembered. Extracted {data.get('extracted', 0)} new fact(s); "
            f"{data.get('total_facts', '?')} live facts total.")


@mcp.tool(
    name="engram_close_session",
    annotations=ToolAnnotations(title="Close a memory session", **_WRITE),
)
async def engram_close_session(
    session_id: Annotated[str, Field(description="Conversation/thread id to close.")] = "default",
    summarize: Annotated[
        bool,
        Field(description="Create missing session summaries for future lean recall."),
    ] = True,
    clear_working: Annotated[
        bool,
        Field(description="Clear ephemeral working memory for this session."),
    ] = True,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """Finish the memory lifecycle for a client thread without deleting the raw transcript.

    Call this when Claude Code / Codex / Cursor is done with a task or is switching sessions. It
    drains pending consolidation for this session, creates missing summaries, reflects knowledge
    updates into summaries, clears short-lived working memory, and persists the namespace.

    Returns (json): {"ok","session_id","episodes","pending_consolidated","facts_added",
    "summaries","working_cleared",...}.
    """
    try:
        data = await backend().close_session(
            session_id=session_id,
            summarize=summarize,
            clear_working=clear_working,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if response_format == ResponseFormat.JSON:
        return _json(data)
    return (
        f"Closed session `{data.get('session_id', session_id)}`. "
        f"Processed {data.get('episodes', 0)} episode(s), "
        f"consolidated {data.get('pending_consolidated', 0)} pending episode(s), "
        f"added {data.get('facts_added', 0)} fact(s), "
        f"created {data.get('summaries', 0)} summary(ies), "
        f"cleared {data.get('working_cleared', 0)} working item(s)."
    )


@mcp.tool(name="engram_session_report", annotations=ToolAnnotations(title="Audit a memory session", **_READ))
async def engram_session_report(
    session_id: Annotated[str, Field(description="Conversation/thread id to audit.", min_length=1)],
    include_sensitive: Annotated[
        bool,
        Field(description="When false (default), redact sensitive fact text from the report."),
    ] = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """Show what durable facts a specific session contributed to memory.

    Use this when the user asks "what did you save from this session?" or after closing a session for a
    visible audit. Sensitive facts are redacted by default; set include_sensitive=true only when the user
    explicitly wants a full private audit.

    Returns (json): {"ok","session_id","episodes","facts_added","facts_redacted","facts":[...]}.
    """
    try:
        data = await backend().session_report(
            session_id=session_id,
            include_sensitive=include_sensitive,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if response_format == ResponseFormat.JSON:
        return _json(data)
    facts = data.get("facts") or []
    lines = [
        "# Engram session report",
        "",
        f"- Session: `{data.get('session_id', session_id)}`",
        f"- Episodes: {data.get('episodes', 0)} "
        f"({data.get('episodes_pending', 0)} pending consolidation)",
        f"- Working memory still live: {data.get('working_live', 0)} item(s)",
        f"- Durable facts linked to this session: {data.get('facts_added', 0)}",
        f"- Sensitive facts redacted: {data.get('facts_redacted', 0)}",
    ]
    if not facts:
        lines += ["", "No durable facts are linked to this session yet."]
        return "\n".join(lines)
    lines += ["", "Facts:"]
    for f in facts:
        tag = " _(superseded)_" if f.get("status") != "live" else ""
        redacted = " 🔒 redacted" if f.get("redacted") else ""
        lines.append(f"- `{f.get('id', '?')}` [{f.get('valid_at', '?')}] {f.get('text', '')}{tag}{redacted}")
    return "\n".join(lines)


@mcp.tool(name="engram_list_sessions", annotations=ToolAnnotations(title="List memory sessions", **_READ))
async def engram_list_sessions(
    limit: Annotated[int, Field(description="Max sessions to return. Default 20.", ge=1, le=100)] = 20,
    offset: Annotated[int, Field(description="Sessions to skip (pagination).", ge=0)] = 0,
    q: Annotated[str, Field(description="Optional substring filter over session ids, e.g. 'codex'.")] = "",
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """List the content-free cross-agent/app session index.

    Use this when the user asks which Codex / Claude Code / app sessions have touched their memory.
    This returns session ids, timestamps, and counts only — no raw episode text, summaries, profile prose,
    or fact text. Call `engram_session_report(session_id)` to audit one session's durable facts.

    Returns (json): {"ok","sessions":[...],"page":...}.
    """
    try:
        data = await backend().sessions(limit=limit, offset=offset, q=q)
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if response_format == ResponseFormat.JSON:
        return _json(data)
    sessions = data.get("sessions") or []
    page = data.get("page") or {}
    total = page.get("total", len(sessions))
    if not sessions:
        return "No memory sessions found." if not q else f"No memory sessions found matching `{q}`."
    start = offset + 1
    lines = [f"# Memory sessions ({start}–{offset + len(sessions)} of {total})", ""]
    for row in sessions:
        sid = row.get("id", "?")
        parts = [
            f"{row.get('episodes', 0)} episode(s)",
            f"{row.get('facts_added', 0)} fact(s)",
            f"{row.get('working_live', 0)} working",
        ]
        if row.get("episodes_pending", 0):
            parts.append(f"{row.get('episodes_pending')} pending")
        if row.get("facts_sensitive", 0):
            parts.append(f"{row.get('facts_sensitive')} sensitive")
        last = row.get("last_event_at_h") or "no timestamp"
        lines.append(f"- `{sid}` — {', '.join(parts)}; last: {last}")
    nxt = page.get("next_offset")
    if nxt is not None:
        lines += ["", f"…more sessions — call again with offset={nxt}."]
    lines += ["", "Use `engram_session_report(session_id=...)` to audit one session."]
    return "\n".join(lines)


@mcp.tool(name="engram_list_facts", annotations=ToolAnnotations(title="List stored facts", **_READ))
async def engram_list_facts(
    limit: Annotated[int, Field(description="Max facts to return. Default 20.", ge=1, le=100)] = 20,
    offset: Annotated[int, Field(description="Facts to skip (pagination).", ge=0)] = 0,
    include_superseded: Annotated[bool, Field(description="Also show old facts that were superseded by a "
                                              "newer value (the bi-temporal history).")] = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """Browse the atomic facts Engram has stored about the user (paginated).

    Each fact is dated (valid-from) and marked live or superseded; user-asserted facts carry a 🔒.
    Useful to audit what's known, or to find a fact id before correcting it.

    Returns (json): {"total","count","offset","has_more","next_offset","facts":[...]}.
    """
    try:
        dump = await backend().memories()
    except Exception as e:  # noqa: BLE001
        return _err(e)
    facts = dump.get("facts", [])
    if not include_superseded:
        facts = [f for f in facts if f.get("status") == "live"]
    total = len(facts)
    page = facts[offset:offset + limit]
    nxt = offset + len(page) if offset + len(page) < total else None
    if response_format == ResponseFormat.JSON:
        return _json({"total": total, "count": len(page), "offset": offset,
                      "has_more": nxt is not None, "next_offset": nxt, "facts": page})
    if not page:
        return "No facts stored yet." if total == 0 else "No facts on this page."
    lines = [f"# Stored facts ({offset + 1}–{offset + len(page)} of {total})", ""]
    for f in page:
        tag = "" if f.get("status") == "live" else " _(superseded)_"
        lock = " 🔒" if f.get("source") == "user" else ""
        lines.append(f"- `{f.get('id', '?')}` [{f.get('valid_at', '?')}] {f.get('text', '')}{tag}{lock}")
    if nxt is not None:
        lines += ["", f"…{total - nxt} more — call again with offset={nxt}."]
    return "\n".join(lines)


@mcp.tool(name="engram_profile", annotations=ToolAnnotations(title="Get the user profile", **_READ))
async def engram_profile(response_format: ResponseFormat = ResponseFormat.MARKDOWN) -> str:
    """Get the synthesized profile of the user (a compact narrative of their durable preferences,
    habits, and key facts) — a good first call at the start of a session to orient yourself.

    Returns (json): {"profile": str, "facts": [str, ...]}.
    """
    try:
        data = await backend().profile()
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if response_format == ResponseFormat.JSON:
        return _json(data)
    profile = (data.get("profile") or "").strip()
    out = ["# User profile", "", profile or "(no profile synthesized yet — memory may be empty)"]
    facts = data.get("facts") or []
    if facts:
        out += ["", "Key facts:"] + [f"- {t}" for t in facts[:20]]
    return "\n".join(out)


@mcp.tool(name="engram_get_focus", annotations=ToolAnnotations(title="Get memory focus", **_READ))
async def engram_get_focus(response_format: ResponseFormat = ResponseFormat.MARKDOWN) -> str:
    """Inspect the user's focus policy: tracked topics are boosted in retrieval/salience, and muted
    topics are suppressed from recall/profile contexts.

    Returns (json): {"track": [str, ...], "mute": [str, ...]}.
    """
    try:
        focus = await backend().get_focus()
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if response_format == ResponseFormat.JSON:
        return _json(focus)
    return "# Memory focus\n\n" + _format_focus(focus)


@mcp.tool(name="engram_set_focus", annotations=ToolAnnotations(title="Set memory focus", **_WRITE))
async def engram_set_focus(
    track: Annotated[Optional[list[str]], Field(description="Topics Engram should emphasize. Omit to leave "
                                                 "unchanged; pass [] to clear tracked topics.")] = None,
    mute: Annotated[Optional[list[str]], Field(description="Topics Engram should suppress from recall/profile. "
                                                "Omit to leave unchanged; pass [] to clear muted topics.")] = None,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """Update the user's memory focus policy.

    Use this when the user says things like "pay more attention to my project decisions" or "stop
    surfacing health details unless I ask." `track` topics get a salience boost; `mute` topics are
    hidden from assembled recall/profile context. Passing None leaves that list unchanged; passing []
    clears it.

    Returns (json): {"ok": true, "focus": {"track": [...], "mute": [...]}}.
    """
    if track is None and mute is None:
        return "Error: provide `track` or `mute`. Pass [] to clear a list."
    try:
        data = await backend().set_focus(track=track, mute=mute)
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if response_format == ResponseFormat.JSON:
        return _json(data)
    focus = data.get("focus") or {}
    return "# Updated memory focus\n\n" + _format_focus(focus)


@mcp.tool(name="engram_agent_status", annotations=ToolAnnotations(title="Get agent memory status", **_READ))
async def engram_agent_status(
    session_id: Annotated[Optional[str], Field(description="Optional current conversation/thread id. "
                                           "When set, session-specific counts are included.")] = None,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """Inspect the content-free memory contract for this agent session.

    Use this at startup or when debugging whether Engram is attached correctly. It does not return
    profile text, fact text, summaries, or raw episodes; it only returns namespace/session counts, focus
    policy, and recommended next tool calls.

    Returns (json): {"ok","user","session_id","focus","session","counts","recommended_next_actions",...}.
    """
    try:
        data = await backend().agent_status(session_id=session_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if response_format == ResponseFormat.JSON:
        return _json(data)
    c = data.get("counts") or {}
    s = data.get("session") or {}
    focus = data.get("focus") or {}
    lines = [
        "# Engram agent status",
        "",
        f"- Namespace: `{data.get('user', '?')}`",
        f"- Session: `{s.get('id') or '(none supplied)'}`",
        f"- Session episodes: {s.get('episodes', 0)} "
        f"({s.get('episodes_pending', 0)} pending consolidation)",
        f"- Session working memory: {s.get('working_live', 0)} live item(s)",
        f"- Long-term memory: {c.get('facts_live', 0)} live fact(s), "
        f"{c.get('summaries', 0)} summary(ies), {c.get('pending_conflicts', 0)} pending conflict(s)",
        f"- Overall working memory: {c.get('working_live', 0)} live item(s)",
        f"- Consolidation backlog: {'yes' if data.get('consolidation_backlog') else 'no'}",
        "- Focus:",
        _format_focus(focus),
        "",
        "Recommended next actions:",
    ]
    lines += [f"- {item}" for item in data.get("recommended_next_actions", [])]
    return "\n".join(lines)


@mcp.tool(name="engram_stats", annotations=ToolAnnotations(title="Get memory stats", **_READ))
async def engram_stats(response_format: ResponseFormat = ResponseFormat.MARKDOWN) -> str:
    """Get content-free namespace observability: episode counts, consolidation backlog, fact counts,
    graph counts, pending conflicts, and backend readiness. Use this for health checks and progress
    audits without exposing profile text, facts, or raw episodes.

    Returns (json): MemoryStats from `/v1/stats`.
    """
    try:
        data = await backend().stats()
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if response_format == ResponseFormat.JSON:
        return _json(data)
    c = data.get("counts") or {}
    tr = data.get("time_range") or {}
    lines = [
        "# Engram stats",
        "",
        f"- Episodes: {c.get('episodes', 0)} "
        f"({c.get('episodes_consolidated', 0)} consolidated, {c.get('episodes_pending', 0)} pending, "
        f"{c.get('episodes_ephemeral', 0)} ephemeral)",
        f"- Facts: {c.get('facts_live', 0)} live, {c.get('facts_superseded', 0)} superseded, "
        f"{c.get('facts_sensitive', 0)} sensitive",
        f"- Heat tiering: {c.get('facts_hot', 0)} hot, {c.get('facts_cold', 0)} cold, "
        f"{c.get('cold_pages_out', 0)} paged out, {c.get('cold_pages_in', 0)} paged back",
        f"- Working memory: {c.get('working_live', 0)} live",
        f"- Graph: {c.get('entities', 0)} entities, {c.get('relations', 0)} relations",
        f"- Graph hygiene: {c.get('graph_orphan_entities', 0)} orphan entities, "
        f"{c.get('graph_stale_relations', 0)} stale relations",
        f"- Pending conflicts: {c.get('pending_conflicts', 0)}",
        f"- Consolidation backlog: {'yes' if data.get('consolidation_backlog') else 'no'}",
        f"- Backend: storage={data.get('storage', '?')}, embedder={data.get('embedder', '?')}, "
        f"llm_configured={bool(data.get('llm_configured'))}",
    ]
    if tr.get("first_event_at_h") or tr.get("last_event_at_h"):
        lines.append(f"- Event range: {tr.get('first_event_at_h') or '?'} to {tr.get('last_event_at_h') or '?'}")
    return "\n".join(lines)


@mcp.tool(name="engram_add_fact", annotations=ToolAnnotations(title="Assert an authoritative fact", **_WRITE))
async def engram_add_fact(
    predicate: Annotated[str, Field(description="The relation, snake_case, e.g. 'works_at', 'lives_in', "
                                    "'prefers', 'allergic_to'.", min_length=1)],
    object: Annotated[str, Field(description="The value, e.g. 'Acme Corp', 'Berlin', 'dark mode'.",
                                 min_length=1)],
    subject: Annotated[str, Field(description="Who/what the fact is about. Default 'user'.")] = "user",
    sensitive: Annotated[Optional[bool], Field(description="Optional user override for sensitivity.")] = None,
    category: Annotated[Optional[str], Field(description="Optional user-visible category label.")] = None,
) -> str:
    """Assert a single authoritative (subject, predicate, object) fact.

    Unlike `engram_remember` (which extracts facts from free text), this records one exact fact the user
    has explicitly asserted. It is marked user-authored and is AUTHORITATIVE: automatic extraction will
    never silently overwrite it, and it supersedes any conflicting extracted value on the same slot.
    """
    try:
        data = await backend().add_fact(subject, predicate, object, sensitive=sensitive, category=category)
    except Exception as e:  # noqa: BLE001
        return _err(e)
    return f"Stored authoritative fact: {data.get('text', f'{subject} {predicate} {object}')}"


@mcp.tool(name="engram_update_fact", annotations=ToolAnnotations(title="Correct a stored fact", **_WRITE))
async def engram_update_fact(
    fact_id: Annotated[str, Field(description="Fact id from engram_list_facts.", min_length=1)],
    subject: Annotated[Optional[str], Field(description="Optional replacement subject.")] = None,
    predicate: Annotated[Optional[str], Field(description="Optional replacement predicate.")] = None,
    object: Annotated[Optional[str], Field(description="Optional replacement object/value.")] = None,
    sensitive: Annotated[Optional[bool], Field(description="Optional sensitivity override.")] = None,
    category: Annotated[Optional[str], Field(description="Optional category override.")] = None,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """Correct one stored fact by id.

    Use this when the user says a specific remembered fact is wrong. The edit becomes user-authored and
    authoritative, so later automatic extraction will not silently overwrite it.

    Returns (json): {"ok": true, "id": str, "text": str} or an error string if not found.
    """
    if all(v is None for v in (subject, predicate, object, sensitive, category)):
        return "Error: provide at least one field to update."
    try:
        data = await backend().update_fact(
            fact_id,
            subject=subject,
            predicate=predicate,
            object=object,
            sensitive=sensitive,
            category=category,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if data is None:
        return f"Error: fact `{fact_id}` was not found."
    if response_format == ResponseFormat.JSON:
        return _json(data)
    return f"Updated fact `{data.get('id', fact_id)}`: {data.get('text', '')}"


@mcp.tool(name="engram_delete_fact", annotations=ToolAnnotations(title="Delete one stored fact", **_DESTRUCTIVE))
async def engram_delete_fact(
    fact_id: Annotated[str, Field(description="Fact id from engram_list_facts.", min_length=1)],
    confirm: Annotated[bool, Field(description="Must be true to permanently delete this fact.")] = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """Permanently delete one stored fact by id.

    This is for precise correction/right-to-forget requests. It does not erase the whole namespace; use
    `engram_forget(confirm=true)` only when the user explicitly asks to wipe everything.

    Returns (json): {"ok": bool}.
    """
    if not confirm:
        return (
            f"This permanently deletes fact `{fact_id}` and cannot be undone. Re-call with confirm=true "
            "only if the user explicitly asked to delete that fact."
        )
    try:
        data = await backend().delete_fact(fact_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if response_format == ResponseFormat.JSON:
        return _json(data)
    return f"Deleted fact `{fact_id}`." if data.get("ok") else f"Fact `{fact_id}` was not found."


@mcp.tool(name="engram_import", annotations=ToolAnnotations(title="Bulk-import a history", **_WRITE))
async def engram_import(
    content: Annotated[str | list | dict,
                       Field(description="The raw export to import: a ChatGPT conversations.json, an "
                             "OpenAI messages array, JSON-Lines, a plain 'Speaker: text' transcript, or "
                             "a native Engram export from engram_export (restores facts/episodes with "
                             "their ids and history intact — the migration path between instances). "
                             "Paste the file contents (JSON text, an array, or transcript text).")],
    format: Annotated[str, Field(description="chatgpt | messages | records | jsonl | transcript | "
                                 "engram | auto (default, sniffs the shape).")] = "auto",
) -> str:
    """Bulk-import an exported chat history into memory in one batched pass (extract facts + summaries).

    Use this to seed memory from an existing history (e.g. the user's ChatGPT export) rather than
    replaying it message by message. A native Engram export restores directly instead of re-extracting.
    `format='auto'` detects the shape.
    """
    try:
        data = await backend().import_(content, format=format)
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if data.get("format") == "engram":
        return (f"Restored Engram export: {data.get('facts', 0)} fact(s) "
                f"({data.get('facts_skipped', 0)} already present), {data.get('episodes', 0)} episode(s) "
                f"({data.get('episodes_skipped', 0)} already present), "
                f"{data.get('summaries', 0)} summary(ies).")
    return (f"Imported {data.get('sessions', 0)} session(s) / {data.get('episodes', 0)} episode(s); "
            f"extracted {data.get('facts_added', 0)} fact(s), {data.get('summaries', 0)} summary(ies).")


@mcp.tool(name="engram_export", annotations=ToolAnnotations(title="Export memory data", **_READ))
async def engram_export(
    include_sensitive: Annotated[
        bool,
        Field(description="When false (default), return a share-safe structured export: non-sensitive "
              "facts + graph only. Set true only when the user explicitly wants a full private export."),
    ] = False,
    response_format: ResponseFormat = ResponseFormat.MARKDOWN,
) -> str:
    """Export the user's memory for portability or audit.

    Default is share-safe: sensitive facts and free-text layers (profile, raw episodes, summaries) are
    omitted. Set include_sensitive=true only for an explicit user-owned private export.

    Returns (json): the `/v1/export` payload.
    """
    try:
        data = await backend().export(include_sensitive=include_sensitive)
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if response_format == ResponseFormat.JSON:
        return _json(data)
    facts = data.get("facts") or []
    graph = data.get("graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    lines = [
        "# Engram export",
        "",
        f"- Version: {data.get('engram_export_version', '?')}",
        f"- Namespace: `{data.get('user', '?')}`",
        f"- Include sensitive: {bool(data.get('include_sensitive'))}",
        f"- Redacted sensitive: {bool(data.get('redacted_sensitive'))}",
        f"- Facts: {len(facts)}",
        f"- Graph: {len(nodes)} node(s), {len(edges)} edge(s)",
    ]
    if data.get("redaction_note"):
        lines += ["", data["redaction_note"]]
    lines += [
        "",
        "Call again with `response_format=\"json\"` to get the portable JSON payload.",
    ]
    return "\n".join(lines)


@mcp.tool(name="engram_forget", annotations=ToolAnnotations(title="Erase all memory", **_DESTRUCTIVE))
async def engram_forget(
    confirm: Annotated[bool, Field(description="Must be true to proceed — this is irreversible.")] = False,
) -> str:
    """Permanently erase ALL memory in this namespace (right-to-forget). Irreversible.

    Requires confirm=true. Without it, this is a no-op that explains what would happen — so a stray call
    can't wipe the user's memory.
    """
    if not confirm:
        return ("This permanently erases ALL stored memory for this namespace and cannot be undone. "
                "Re-call with confirm=true only if the user explicitly asked to wipe their memory.")
    try:
        data = await backend().forget()
    except Exception as e:  # noqa: BLE001
        return _err(e)
    return data.get("message", "All memory erased.")
