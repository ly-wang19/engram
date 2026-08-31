"""Batch-import connectors (CLAUDE.md §6 adoption layer): turn an external history export into Engram
sessions, then `Memory.import_messages()` ingests them in a few batched calls.

    from engram import Memory
    from engram.connectors import parse

    sessions = parse(open("conversations.json").read(), format="chatgpt")
    Memory().import_messages(sessions, user_id="me")

`format="auto"` (the default) sniffs the shape. Supported formats:
    chatgpt     ChatGPT data export (conversations.json)
    messages    OpenAI chat array — [{role, content}, ...] (or {messages:[...]})
    records     flat list of {content, [session_id], [speaker], [timestamp]}
    jsonl       JSON-Lines, one record per line
    transcript  plain text / markdown ("Speaker: text" lines, or freeform)
"""
from __future__ import annotations

from typing import Any

from .base import ImportMessage, ImportSession, extract_text, load_json, to_epoch
from .chatgpt import parse_chatgpt
from .agent_sessions import find_sessions, load_sessions, parse_agent_session
from .generic import parse_jsonl, parse_messages, parse_records, parse_transcript

__all__ = [
    "ImportMessage", "ImportSession", "parse", "sniff",
    "parse_chatgpt", "parse_messages", "parse_records", "parse_jsonl", "parse_transcript",
    "parse_agent_session", "find_sessions", "load_sessions",
    "extract_text", "to_epoch",
]

FORMATS = ("chatgpt", "messages", "records", "jsonl", "transcript", "agent_session")


def sniff(data: Any) -> str:
    """Guess the format from the data's shape. Falls back to 'transcript' for free text."""
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        s = data.lstrip()
        if not s:
            return "transcript"
        if s[0] not in "[{":
            return "transcript"  # plain text / markdown
        # multiple lines each starting with '{' => JSON-Lines
        lines = [ln for ln in data.splitlines() if ln.strip()]
        if len(lines) > 1 and all(ln.lstrip().startswith("{") for ln in lines):
            # Agent session logs are JSONL too, but the generic reader would keep their tool calls and
            # drop the conversation, so detect them by their distinctive envelope first.
            head = "\n".join(lines[:60])
            if '"isSidechain"' in head or '"type": "response_item"' in head or '"type":"response_item"' in head:
                return "agent_session"
            return "jsonl"
        try:
            data = load_json(data)
        except Exception:  # noqa: BLE001
            return "transcript"
    return _sniff_obj(data)


def _sniff_obj(obj: Any) -> str:
    if isinstance(obj, dict):
        if "engram_export_version" in obj:
            return "engram"  # our own /v1/export snapshot -- restored, not parsed as messages
        if "mapping" in obj or "conversations" in obj:
            return "chatgpt"
        if "messages" in obj:
            return "messages"
        return "records"
    if isinstance(obj, list):
        sample = next((x for x in obj if isinstance(x, dict)), None)
        if sample is None:
            return "records"
        if "mapping" in sample:
            return "chatgpt"
        if "messages" in sample:
            return "messages"
        if any(k in sample for k in ("role", "speaker", "author", "from")) and \
                any(k in sample for k in ("content", "text", "message", "body")):
            return "messages" if not any(
                k in sample for k in ("session_id", "conversation_id", "thread_id")) else "records"
        return "records"
    return "transcript"


def parse(data: Any, format: str = "auto", session_id: str = "imported") -> list[ImportSession]:
    """Parse a raw export (object, JSON string, or bytes) into a list of ImportSession.

    Args:
        data: the export — a Python object, a JSON/JSONL string, or bytes.
        format: one of FORMATS, or 'auto' (default) to sniff it.
        session_id: base session id for formats that don't carry their own.

    Raises ValueError on an unknown format or a shape that doesn't match the named format.
    """
    fmt = format.lower().strip()
    if fmt == "auto":
        fmt = sniff(data)

    if fmt == "transcript" or fmt in ("text", "markdown", "md"):
        text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        return parse_transcript(text, session_id=session_id)
    if fmt in ("agent_session", "claude_code", "codex"):
        # A Claude Code / Codex session log: mostly tool machinery, so it needs its own reader rather
        # than the generic JSONL one (which would keep the tool calls and lose the conversation).
        text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        return parse_agent_session(text, session_id=session_id)
    if fmt == "jsonl":
        text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        return parse_jsonl(text, session_id=session_id)

    obj = load_json(data)
    if fmt == "engram":
        raise ValueError(
            "this is an Engram export snapshot, not a message history -- restore it with "
            "Memory.import_snapshot() (or POST it to /v1/import, which routes it automatically).")
    if fmt == "chatgpt":
        return parse_chatgpt(obj)
    if fmt in ("messages", "openai"):
        return parse_messages(obj, session_id=session_id)
    if fmt in ("records", "generic"):
        return parse_records(obj, session_id=session_id)
    raise ValueError(f"unknown import format {format!r}; expected one of {FORMATS} or 'auto'.")
