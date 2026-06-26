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
    "`engram_remember` whenever the user states a durable fact, preference, or decision worth keeping. "
    "`engram_search` gives a single direct answer; `engram_stats` checks content-free memory health; "
    "`engram_list_facts` / `engram_profile` browse what's stored; `engram_add_fact` records an "
    "authoritative fact; `engram_import` bulk-loads an exported history; `engram_forget` erases "
    "everything (irreversible)."
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
) -> str:
    """Store something in the user's long-term memory and consolidate it (extract atomic bi-temporal
    facts, resolve contradictions against what's already known, update the knowledge graph).

    Call this whenever the user states a durable fact, preference, plan, or decision worth remembering
    across sessions. Storing a value that contradicts an older one does NOT delete history — the old
    fact is invalidated and kept (ask-as-of still works). Best-effort: the raw memory is saved even if
    consolidation degrades.
    """
    try:
        data = await backend().remember(content, session_id=session_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)
    if data.get("degraded"):
        return (f"Stored the raw memory, but consolidation degraded ({data['degraded']}). It is still "
                "recallable.")
    return (f"Remembered. Extracted {data.get('extracted', 0)} new fact(s); "
            f"{data.get('total_facts', '?')} live facts total.")


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
        lines.append(f"- [{f.get('valid_at', '?')}] {f.get('text', '')}{tag}{lock}")
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
) -> str:
    """Assert a single authoritative (subject, predicate, object) fact.

    Unlike `engram_remember` (which extracts facts from free text), this records one exact fact the user
    has explicitly asserted. It is marked user-authored and is AUTHORITATIVE: automatic extraction will
    never silently overwrite it, and it supersedes any conflicting extracted value on the same slot.
    """
    try:
        data = await backend().add_fact(subject, predicate, object)
    except Exception as e:  # noqa: BLE001
        return _err(e)
    return f"Stored authoritative fact: {data.get('text', f'{subject} {predicate} {object}')}"


@mcp.tool(name="engram_import", annotations=ToolAnnotations(title="Bulk-import a history", **_WRITE))
async def engram_import(
    content: Annotated[str | list | dict,
                       Field(description="The raw export to import: a ChatGPT conversations.json, an "
                             "OpenAI messages array, JSON-Lines, or a plain 'Speaker: text' transcript. "
                             "Paste the file contents (JSON text, an array, or transcript text).")],
    format: Annotated[str, Field(description="chatgpt | messages | records | jsonl | transcript | auto "
                                 "(default, sniffs the shape).")] = "auto",
) -> str:
    """Bulk-import an exported chat history into memory in one batched pass (extract facts + summaries).

    Use this to seed memory from an existing history (e.g. the user's ChatGPT export) rather than
    replaying it message by message. `format='auto'` detects the shape.
    """
    try:
        data = await backend().import_(content, format=format)
    except Exception as e:  # noqa: BLE001
        return _err(e)
    return (f"Imported {data.get('sessions', 0)} session(s) / {data.get('episodes', 0)} episode(s); "
            f"extracted {data.get('facts_added', 0)} fact(s), {data.get('summaries', 0)} summary(ies).")


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
