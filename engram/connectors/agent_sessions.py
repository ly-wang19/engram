"""Agent session transcripts (Claude Code, Codex) -> Engram sessions.

The reason this exists: an agent user's memory-worthy material IS their agent sessions, and those already
sit on disk — ~/.claude/projects/<project>/<session>.jsonl and ~/.codex/sessions/. Before this, getting
them in meant hand-writing sentences into `engram_remember` one at a time, so in practice nothing went in.

These logs are not chat exports. They interleave the conversation with the agent's internal machinery:
thinking blocks, tool calls and their results, queue operations, title generation, permission prompts.
Ingesting all of it would bury the few durable facts under transcript noise — the memory would fill with
"ran grep, got 3 matches". So this keeps what a person actually said and what the agent actually replied,
and drops the rest.

    from engram.connectors.agent_sessions import parse_agent_session, find_sessions
    sessions = parse_agent_session(open(path).read(), session_id="claude-code:my-project")

Sub-agent transcripts (`isSidechain: true`) are dropped by default: they are one agent talking to another
about how to do the task, not the user's own history.
"""
from __future__ import annotations

import json
import re
import os
from typing import Any, Iterator, Optional

from .base import ImportMessage, ImportSession, to_epoch

# Content blocks that are the agent's machinery rather than what it said. `thinking` is deliberately
# excluded: it is reasoning-in-progress, often contradicted by the final answer, and remembering it
# would teach the memory things the agent decided against.
_SKIP_BLOCKS = {"thinking", "redacted_thinking", "tool_use", "tool_result", "image"}

# Record types that carry no conversation at all.
_SKIP_TYPES = {
    "queue-operation", "ai-title", "custom-title", "last-prompt", "attachment",
    "summary", "file-history-snapshot", "system",
}

# A tool result echoed back as a user turn is the harness talking to itself, not the person.
_TOOL_ECHO_PREFIXES = ("<tool_use_result", "<local-command", "Caveat: The messages below")

# Agent sessions routinely contain live credentials: the user pastes a key, or a command echoes an
# .env. Long-term memory is the worst place for them — it is durable, retrievable by every agent, and
# exportable. Redaction happens at ingest, before anything is stored, because a secret that reaches the
# store has already leaked into embeddings, summaries and extracted facts.
_SECRET_PATTERNS = [
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,})"),                       # OpenAI/DeepSeek/Anthropic style
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})"),                    # GitHub tokens
    re.compile(r"\b(xox[baprs]-[A-Za-z0-9\-]{10,})"),                # Slack
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),                           # AWS access key id
    re.compile(r"\b(eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})"),  # JWT
    # KEY=value / TOKEN: value forms, which is how a pasted .env or an export line looks.
    re.compile(r"((?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)\s*[=:]\s*)([^\s\"']{8,})",
               re.IGNORECASE),
]


def redact_secrets(text: str) -> str:
    """Replace anything that looks like a live credential with a marker, keeping the sentence readable."""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: m.group(1) + "[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


def _text_of(content: Any) -> str:
    """Flatten a message body to the text a person would recognise as 'what was said'."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in _SKIP_BLOCKS:
            continue
        # Codex wraps text as {"type": "input_text"|"output_text", "text": ...}; Claude Code uses
        # {"type": "text", "text": ...}. Both land here.
        text = block.get("text") or block.get("content")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def _rows(text: str) -> Iterator[dict]:
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] != "{":
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a truncated final line is normal for a live session; skip it, don't fail
        if isinstance(row, dict):
            yield row


def parse_agent_session(text: str, session_id: str = "agent-session",
                        include_sidechains: bool = False,
                        min_chars: int = 12) -> list[ImportSession]:
    """Parse one Claude Code / Codex session log into a single ImportSession.

    `min_chars` drops one-word turns ("ok", "继续", "yes") — they carry no fact and would otherwise
    dominate the episode count of a long session.
    """
    messages: list[ImportMessage] = []
    first_ts: Optional[float] = None
    for row in _rows(text):
        if row.get("type") in _SKIP_TYPES:
            continue
        if row.get("isSidechain") and not include_sidechains:
            continue
        # A turn whose body is entirely tool machinery flattens to "" and is dropped by min_chars below;
        # that is intended (94 of 162 turns in a real session are tool_use/tool_result/thinking).
        # Claude Code puts the turn in `message`; Codex wraps it as
        # {"type": "response_item", "payload": {"type": "message", "role": ..., "content": [...]}}.
        message = row.get("message")
        if not isinstance(message, dict):
            payload = row.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "message":
                message = payload
            else:
                continue
        role = str(message.get("role") or row.get("type") or "").strip()
        if role not in ("user", "assistant"):
            continue  # Codex also emits "developer"/"system" turns: harness instructions, not memory
        body = _text_of(message.get("content"))
        if len(body) < min_chars:
            continue
        if body.startswith(_TOOL_ECHO_PREFIXES):
            continue
        ts = to_epoch(row.get("timestamp"))
        if first_ts is None:
            first_ts = ts
        messages.append(ImportMessage(content=redact_secrets(body), speaker=role, event_time=ts))
    if not messages:
        return []
    return [ImportSession(session_id=session_id, messages=messages, event_time=first_ts,
                          metadata={"source": "agent_session"})]


def _session_label(path: str) -> str:
    """A readable session id: the project directory plus the transcript's own id.

    Claude Code encodes the project path in the directory name ("-Users-ywwl-Documents-Foo"), so the
    tail is the part a person recognises.
    """
    name = os.path.basename(path)
    if name.startswith("rollout-"):  # Codex: rollout-<iso-date>-<uuid>.jsonl
        return f"codex:{name[8:18]}:{os.path.splitext(name)[0][-8:]}"
    project = os.path.basename(os.path.dirname(path)).strip("-").split("-")[-1] or "project"
    return f"claude-code:{project}:{os.path.splitext(name)[0][:8]}"


def find_sessions(root: Optional[str] = None, limit: Optional[int] = None,
                  min_bytes: int = 2048, since: Optional[float] = None) -> list[str]:
    """Session transcripts on this machine, newest first.

    `min_bytes` skips sessions too short to contain anything durable (a one-question run).
    """
    roots = [root] if root else [
        os.path.expanduser("~/.claude/projects"),
        os.path.expanduser("~/.codex/sessions"),
    ]
    found: list[tuple[float, str]] = []
    for base in roots:
        if not base or not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                if not name.endswith(".jsonl"):
                    continue
                # agent-*.jsonl are sub-agent transcripts (one agent instructing another), not the
                # user's own session; they would flood memory with task-internal chatter.
                if name.startswith("agent-"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if st.st_size < min_bytes:
                    continue
                if since is not None and st.st_mtime < since:
                    continue
                found.append((st.st_mtime, path))
    found.sort(reverse=True)
    paths = [p for _, p in found]
    return paths[:limit] if limit else paths


def load_sessions(paths: list[str], include_sidechains: bool = False) -> list[ImportSession]:
    """Parse several transcripts into sessions, skipping any that yield no conversation."""
    out: list[ImportSession] = []
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        out.extend(parse_agent_session(text, session_id=_session_label(path),
                                       include_sidechains=include_sidechains))
    return out
