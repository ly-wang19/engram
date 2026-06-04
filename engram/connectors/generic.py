"""Generic connectors: OpenAI `messages` arrays, JSONL logs, flat record lists, and plain transcripts.

These cover the long tail of "I have my history in <some shape>" without a bespoke parser each time.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .base import ImportMessage, ImportSession, extract_text, iter_jsonl, to_epoch

# message dicts name the role under any of these; content under any of these
_ROLE_KEYS = ("role", "speaker", "author", "from", "sender")
_CONTENT_KEYS = ("content", "text", "message", "body", "value")
_TIME_KEYS = ("event_time", "timestamp", "time", "created_at", "create_time", "date", "ts")
_SESSION_KEYS = ("session_id", "session", "conversation_id", "thread_id", "chat_id")


def _first(d: dict, keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _msg_from_dict(d: dict, default_role: str = "user") -> Optional[ImportMessage]:
    role = _first(d, _ROLE_KEYS, default_role)
    if isinstance(role, dict):  # e.g. {"author": {"role": "user"}}
        role = role.get("role") or default_role
    text = extract_text(_first(d, _CONTENT_KEYS, ""))
    if not text or not str(text).strip():
        return None
    return ImportMessage(content=str(text).strip(), speaker=str(role),
                         event_time=to_epoch(_first(d, _TIME_KEYS)))


def parse_messages(obj: Any, session_id: str = "imported") -> list[ImportSession]:
    """An OpenAI-style chat array — a list of {role, content} (or a {messages:[...]} wrapper) — as one
    session. Also accepts a list of such conversations (list-of-lists / list-of-{messages})."""
    if isinstance(obj, dict) and "messages" in obj:
        obj = obj["messages"]
    if not isinstance(obj, list):
        raise ValueError("messages format expects a list of {role, content} objects.")

    # list of conversations?  -> recurse, one session each
    if obj and all(isinstance(x, (list, dict)) and (
            isinstance(x, list) or "messages" in x) for x in obj):
        out: list[ImportSession] = []
        for i, conv in enumerate(obj):
            out.extend(parse_messages(conv, session_id=f"{session_id}_{i}"))
        return [s for s in out if s.messages]

    messages = [m for m in (_msg_from_dict(x) for x in obj if isinstance(x, dict)) if m]
    sess = ImportSession(session_id=session_id, messages=messages, metadata={"source": "messages"})
    return [sess] if messages else []


def parse_records(obj: Any, session_id: str = "imported") -> list[ImportSession]:
    """A flat list of record dicts, each {content, [session_id], [speaker], [timestamp]}. Records are
    grouped into sessions by their session id (those without one share `session_id`), preserving order."""
    if not isinstance(obj, list):
        raise ValueError("records format expects a list of objects.")
    grouped: dict[str, ImportSession] = {}
    order: list[str] = []
    for rec in obj:
        if not isinstance(rec, dict):
            continue
        msg = _msg_from_dict(rec)
        if msg is None:
            continue
        sid = str(_first(rec, _SESSION_KEYS, session_id))
        if sid not in grouped:
            grouped[sid] = ImportSession(session_id=sid, metadata={"source": "records"})
            order.append(sid)
        grouped[sid].messages.append(msg)
    return [grouped[s] for s in order if grouped[s].messages]


def parse_jsonl(text: str, session_id: str = "imported") -> list[ImportSession]:
    """JSON-Lines: one JSON object per line. Each line is treated as a record (it may carry its own
    session id / timestamp / role), so a JSONL chat log splits into sessions just like `records`."""
    records = list(iter_jsonl(text))
    return parse_records(records, session_id=session_id)


# A transcript line like "Alice: hello" — capture speaker + text. Names stay short to avoid eating
# real sentence text that merely contains a colon.
_SPEAKER_RE = re.compile(r"^\s*([A-Za-z][\w .'-]{0,30}?)\s*[:：]\s+(.*\S.*)$")


def parse_transcript(text: str, session_id: str = "imported") -> list[ImportSession]:
    """A plain-text/markdown transcript. If lines look like 'Speaker: text', each becomes a turn (and
    continuation lines append to the current turn). Otherwise the whole thing is one user message."""
    messages: list[ImportMessage] = []
    current: Optional[ImportMessage] = None
    saw_speaker = False
    for raw in text.splitlines():
        m = _SPEAKER_RE.match(raw)
        if m:
            saw_speaker = True
            current = ImportMessage(content=m.group(2).strip(), speaker=m.group(1).strip())
            messages.append(current)
        elif current is not None and raw.strip():
            current.content += "\n" + raw.strip()
        elif raw.strip() and not saw_speaker:
            # no speaker structure yet — accumulate into a single running message
            if current is None:
                current = ImportMessage(content=raw.strip(), speaker="user")
                messages.append(current)
            else:
                current.content += "\n" + raw.strip()
    messages = [m for m in messages if m.content.strip()]
    sess = ImportSession(session_id=session_id, messages=messages, metadata={"source": "transcript"})
    return [sess] if messages else []
