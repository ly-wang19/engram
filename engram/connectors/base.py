"""Batch-import connectors — normalize external chat/history exports into Engram sessions.

Everything in `engram.connectors` is **pure stdlib** (no third-party deps), so importing your history
works in the zero-setup install (CLAUDE.md §5). A *parser* turns a raw export — ChatGPT's
`conversations.json`, an OpenAI `messages` array, a JSONL log, a plain transcript — into a list of
`ImportSession`, which `Memory.import_messages()` ingests in a few batched embedding calls + one
System-2 consolidation pass (10x+ faster than per-message `add()`).

The seam this fills: until now the only bulk loader was the eval-only `seed_console.py`. This is the
user-facing "bring your own history" connector the §6 adoption layer needs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Normalized model — every parser emits these, so Memory.import_messages() has
# exactly one shape to ingest regardless of the source format.
# ---------------------------------------------------------------------------


@dataclass
class ImportMessage:
    """One turn/message from a source export."""

    content: str
    speaker: str = "user"  # author role: user | assistant | system | tool | ...
    event_time: Optional[float] = None  # epoch seconds, if the source dated it
    metadata: dict = field(default_factory=dict)


@dataclass
class ImportSession:
    """A conversation/thread: an ordered group of messages that share a session id."""

    session_id: str
    messages: list[ImportMessage] = field(default_factory=list)
    event_time: Optional[float] = None  # session start; defaults to the first dated message
    title: str = ""
    metadata: dict = field(default_factory=dict)

    def start_time(self) -> Optional[float]:
        """The session's world time: its explicit start, else the first dated message."""
        if self.event_time is not None:
            return self.event_time
        for m in self.messages:
            if m.event_time is not None:
                return m.event_time
        return None

    def to_text(self, roles: bool = True) -> str:
        """Render the turns into a single episode body. `roles=True` prefixes each turn with its
        speaker ('user: ...'), which the extractor/retriever read as real text — matching how the eval
        harness ingests a session (one episode per session, turns concatenated)."""
        lines = []
        for m in self.messages:
            text = (m.content or "").strip()
            if not text:
                continue
            lines.append(f"{m.speaker}: {text}" if roles else text)
        body = "\n".join(lines)
        if self.title and self.title.strip():
            body = f"# {self.title.strip()}\n{body}"
        return body


# ---------------------------------------------------------------------------
# Shared helpers (time + content normalization), reused by every parser.
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")


def to_epoch(value: Any) -> Optional[float]:
    """Best-effort coerce a timestamp into epoch seconds. Accepts epoch int/float (auto-detecting
    millisecond/nanosecond scales), ISO-8601 strings ('2024-01-15T10:30:00Z'), and 'YYYY-MM-DD'.
    Returns None when nothing parseable is found (the caller then falls back to a synthetic clock)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):  # guard: bools are ints in Python
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        # Heuristic rescale: treat clearly-too-large numbers as ms / µs / ns since epoch.
        while v > 1e12:
            v /= 1000.0
        return v
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # numeric string → epoch seconds
        try:
            return to_epoch(float(s))
        except ValueError:
            pass
        # ISO-8601 (tolerate a trailing 'Z')
        iso = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
        # loose 'YYYY-MM-DD' anywhere in the string
        m = _DATE_RE.search(s)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            try:
                return datetime(y, mo, d, tzinfo=timezone.utc).timestamp()
            except ValueError:
                return None
    return None


def extract_text(content: Any) -> str:
    """Flatten the many shapes a 'content' field takes into plain text:
      * a plain string                                  -> itself
      * a list of parts (OpenAI multimodal/vision)      -> text parts joined
      * a dict {'parts': [...]} / {'text': ...} (ChatGPT)-> the text within
    Non-text parts (images, tool blobs) are dropped."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if "parts" in content:
            return extract_text(content["parts"])
        for key in ("text", "content", "value"):
            if key in content:
                return extract_text(content[key])
        return ""
    if isinstance(content, (list, tuple)):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict):
                # OpenAI content-part: {'type': 'text', 'text': '...'}
                if part.get("type") in (None, "text", "input_text", "output_text"):
                    out.append(extract_text(part.get("text") or part.get("content") or ""))
                # else: image_url / file / tool parts — skip
        return "\n".join(p for p in out if p)
    return str(content)


def load_json(data: Any) -> Any:
    """Accept already-parsed objects, JSON text, or bytes; return a Python object."""
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    if isinstance(data, str):
        return json.loads(data)
    return data


def iter_jsonl(text: str):
    """Yield parsed objects from JSON-Lines text (one JSON value per non-blank line)."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue
